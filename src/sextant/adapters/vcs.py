"""What the repository's own history says about when a file existed.

A drift guard proves that the code and the registered configuration agree *right
now*. It cannot prove that they were not edited together, after a result was
seen, to make them agree. Nothing inside the process can prove that, because
anything inside the process is written by the same run.

The history can. If the configuration's commit is an ancestor of the commit that
introduced the results, and the configuration had no uncommitted edits when the
run started, then the specification demonstrably existed before the numbers did -
and a reader can verify it from the repository without taking anyone's word for
it. This module reads those two facts out of git.

Read-only, deliberately
------------------------

Nothing here writes, stages, commits or checks anything out. It runs four
read-only plumbing commands and parses their output. A provenance tool that could
alter the history it reports on would be worth nothing.

Failure is a refusal, never a default
--------------------------------------

Every path where git cannot answer raises. There is no "assume it is committed",
no "assume it is clean" and no empty tuple standing in for "the command failed",
because a provenance check that fails open records exactly the same thing as one
that passes, which is invariant 10's collapse in the place where it matters most.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from sextant.domain.errors import SextantError
from sextant.domain.time import Timestamp

#: The plumbing format: SHA, committer date in strict ISO 8601, subject, split on
#: the ASCII unit separator so a subject containing any printable character is
#: still parsed correctly.
UNIT_SEPARATOR = chr(31)
LOG_FORMAT = "%H%x1f%cI%x1f%s"
COMMAND_TIMEOUT_SECONDS = 30


class GitUnavailable(SextantError):
    """Git could not answer. The provenance question is unanswered, not answered."""


class NotCommitted(SextantError):
    """A file the run depends on is untracked, or has edits that are not committed.

    Either way its committed SHA does not describe what the run actually read, so
    citing that SHA in a report would be a false statement about the ordering.
    """


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit, reduced to the three things a provenance line needs."""

    sha: str
    committed_at: Timestamp
    subject: str

    @property
    def short(self) -> str:
        return self.sha[:12]

    def cite(self) -> str:
        """How this commit appears in a report: SHA, instant, subject."""
        return f"{self.short} at {self.committed_at.isoformat()} - {self.subject}"


@dataclass(frozen=True, slots=True)
class GitRepository:
    """Read-only access to one working tree's history.

    The root is always explicit and has no default. A provenance reader with a
    default root is one that can silently report on the wrong repository.
    """

    root: Path

    def _run(self, *arguments: str) -> str:
        """One read-only git command, or a refusal naming what was asked."""
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise GitUnavailable(
                "git is not on the PATH, so the ordering between the registered "
                "specification and the results cannot be established."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitUnavailable(
                f"git {' '.join(arguments)} did not finish within "
                f"{COMMAND_TIMEOUT_SECONDS}s in {self.root}."
            ) from exc
        if completed.returncode != 0:
            raise GitUnavailable(
                f"git {' '.join(arguments)} failed in {self.root} with exit "
                f"{completed.returncode}: {completed.stderr.strip()}"
            )
        return completed.stdout

    def _predicate(self, *arguments: str) -> bool:
        """A git command whose answer is its exit status rather than its output."""
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise GitUnavailable("git is not on the PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitUnavailable(f"git {' '.join(arguments)} timed out in {self.root}.") from exc
        if completed.returncode not in (0, 1):
            raise GitUnavailable(
                f"git {' '.join(arguments)} failed in {self.root} with exit "
                f"{completed.returncode}: {completed.stderr.strip()}"
            )
        return completed.returncode == 0

    def _relative(self, path: Path) -> str:
        """A path as git wants it: relative to the root, forward slashes."""
        candidate = Path(path)
        if candidate.is_absolute():
            candidate = candidate.relative_to(self.root.resolve())
        return candidate.as_posix()

    def head(self) -> Commit:
        """The commit the working tree is currently on."""
        return _parse_commit(self._run("log", "-1", f"--format={LOG_FORMAT}"), "HEAD")

    def is_tracked(self, path: Path) -> bool:
        """Whether git knows about this file at all."""
        return self._predicate("ls-files", "--error-unmatch", "--", self._relative(path))

    def has_uncommitted_changes(self, path: Path) -> bool:
        """Whether the file differs from what the last commit says it contains.

        Covers both staged and unstaged edits: either means the committed SHA
        describes different bytes from the ones the run read.
        """
        return bool(self._run("status", "--porcelain", "--", self._relative(path)).strip())

    def last_commit_touching(self, path: Path) -> Commit | None:
        """The most recent commit that changed this file, or ``None`` if never.

        ``None`` here is a genuine empty answer - the file has no history - and is
        distinct from the failures above, which raise.
        """
        output = self._run("log", "-1", f"--format={LOG_FORMAT}", "--", self._relative(path))
        if not output.strip():
            return None
        return _parse_commit(output, self._relative(path))

    def is_ancestor(self, earlier: str, later: str) -> bool:
        """Whether ``earlier`` is reachable from ``later``: did it come first?"""
        return self._predicate("merge-base", "--is-ancestor", earlier, later)

    def require_committed(self, path: Path) -> Commit:
        """The commit a run may cite for this file, or a refusal to start.

        Raises when the file is untracked or dirty, because in either case there
        is no committed SHA that describes what the run is about to read. This is
        the gate a runner calls before it computes anything.
        """
        relative = self._relative(path)
        if not self.is_tracked(path):
            raise NotCommitted(
                f"{relative} is not tracked by git. A pre-registration that is not committed "
                "before the run cannot be shown to have existed before the results, so the "
                "run is refused. Commit it first."
            )
        if self.has_uncommitted_changes(path):
            raise NotCommitted(
                f"{relative} has uncommitted changes. Its committed SHA describes different "
                "bytes from the ones this run would read, so citing that SHA in the report "
                "would be a false statement about the ordering. Commit the change - as a new "
                "pre-registered version, never as an edit to a version a result already "
                "exists against - and run again."
            )
        commit = self.last_commit_touching(path)
        if commit is None:
            raise NotCommitted(f"{relative} is tracked but has no commit that introduced it.")
        return commit


def _parse_commit(output: str, what: str) -> Commit:
    """One log line into a commit, or a refusal naming what was being read."""
    line = output.strip()
    fields = line.split(UNIT_SEPARATOR)
    expected_fields = 3
    if len(fields) != expected_fields:
        raise GitUnavailable(
            f"git log for {what} returned {len(fields)} fields rather than {expected_fields}: "
            f"{line!r}"
        )
    sha, committed, subject = fields
    return Commit(sha=sha, committed_at=Timestamp.parse(committed), subject=subject)


__all__ = [
    "Commit",
    "GitRepository",
    "GitUnavailable",
    "NotCommitted",
]
