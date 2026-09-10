"""Whether the specification demonstrably predates the numbers.

The drift guard proves the code and the registered configuration agree at run
time. It cannot prove they were not edited together after a result was seen,
because a check that runs inside the run is written by the same run. Git ordering
can prove it, and these tests are about the four facts that do it: the
configuration is tracked, it is clean, its commit exists, and its commit is an
ancestor of the commit that introduced the results.

The test that carries the most weight is
``test_a_configuration_committed_after_the_results_is_not_verified``. That is the
exact shape of the failure the Sponsor asked for a mechanism against - the config
written or widened once the numbers were known - and the audit has to answer NO,
not "probably fine".

Every repository here is built under ``tmp_path``. Nothing touches the project's
own history, and nothing here writes, stages or commits in the real repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sextant.adapters.vcs import GitRepository, GitUnavailable, NotCommitted
from sextant.app.spike_006_f1 import ordering_audit, registration_provenance

CONFIG = Path("config") / "spike.yaml"
RESULTS = Path("research") / "results.json"
IDENTITY = (
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.invalid",
    "-c",
    "commit.gpgsign=false",
)


def git(root: Path, *arguments: str) -> None:
    """One git command in the temporary repository, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(root), *IDENTITY, *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {completed.stderr}")


def write(root: Path, relative: Path, text: str) -> None:
    """A file inside the temporary repository, parents created."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """An empty repository with one unrelated commit, so HEAD exists."""
    git(tmp_path, "init", "-q")
    write(tmp_path, Path("README.md"), "a repository\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-q", "-m", "chore: a repository")
    return tmp_path


def commit_config(root: Path, *, body: str = "version: v1\n") -> None:
    write(root, CONFIG, body)
    git(root, "add", CONFIG.as_posix())
    git(root, "commit", "-q", "-m", "chore: the pre-registration, before any result")


def commit_results(root: Path, *, body: str = '{"return": 0.42}\n') -> None:
    write(root, RESULTS, body)
    git(root, "add", RESULTS.as_posix())
    git(root, "commit", "-q", "-m", "feat: the results")


# ---------------------------------------------------------------------------
# The gate the runner passes through before it computes anything
# ---------------------------------------------------------------------------


def test_an_untracked_configuration_refuses_the_run(repository: Path) -> None:
    """A specification git has never seen cannot be shown to have come first."""
    write(repository, CONFIG, "version: v1\n")
    with pytest.raises(NotCommitted, match="not tracked"):
        registration_provenance(root=repository, config_path=CONFIG)


def test_a_configuration_with_uncommitted_edits_refuses_the_run(repository: Path) -> None:
    """The committed SHA would describe different bytes from the ones read.

    This is the loophole the ordering audit would otherwise leave wide open: commit
    the configuration, then edit it, then run and cite the clean-looking SHA.
    """
    commit_config(repository)
    write(repository, CONFIG, "version: v1\nequity: 999999\n")
    with pytest.raises(NotCommitted, match="uncommitted changes"):
        registration_provenance(root=repository, config_path=CONFIG)


def test_a_staged_but_uncommitted_edit_also_refuses(repository: Path) -> None:
    """Staged is not committed. The distinction matters and is easy to blur."""
    commit_config(repository)
    write(repository, CONFIG, "version: v1\nequity: 999999\n")
    git(repository, "add", CONFIG.as_posix())
    with pytest.raises(NotCommitted, match="uncommitted changes"):
        registration_provenance(root=repository, config_path=CONFIG)


def test_a_committed_clean_configuration_yields_the_commit_to_cite(repository: Path) -> None:
    """The success path, and what the results file records."""
    commit_config(repository)
    provenance = registration_provenance(root=repository, config_path=CONFIG)
    payload = provenance.as_json()
    assert payload["config_path"] == CONFIG.as_posix()
    assert isinstance(payload["config_commit_sha"], str)
    assert len(str(payload["config_commit_sha"])) == 40
    assert payload["config_was_clean_at_run_time"] is True
    assert "pre-registration" in str(payload["config_commit_subject"])


def test_an_unrelated_change_elsewhere_does_not_make_the_config_dirty(
    repository: Path,
) -> None:
    """The check is scoped to the file, so ordinary work in progress is fine."""
    commit_config(repository)
    write(repository, Path("src") / "scratch.py", "x = 1\n")
    provenance = registration_provenance(root=repository, config_path=CONFIG)
    assert provenance.config_commit.sha


def test_a_directory_that_is_not_a_repository_raises_rather_than_defaulting(
    tmp_path: Path,
) -> None:
    """A provenance check that fails open records the same thing as one that passes."""
    with pytest.raises(GitUnavailable):
        registration_provenance(root=tmp_path, config_path=CONFIG)


# ---------------------------------------------------------------------------
# The audit the report quotes
# ---------------------------------------------------------------------------


def test_results_not_yet_committed_is_reported_as_not_verifiable(repository: Path) -> None:
    """Running the audit too early says so rather than claiming verification."""
    commit_config(repository)
    write(repository, RESULTS, '{"return": 0.42}\n')
    audit = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    assert audit.results_commit is None
    assert audit.config_precedes_results is None
    assert audit.is_verified is False
    assert any("NOT YET COMMITTED" in line for line in audit.lines())


def test_a_configuration_committed_first_is_verified(repository: Path) -> None:
    """The ordering the whole mechanism exists to establish."""
    commit_config(repository)
    commit_results(repository)
    audit = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    assert audit.results_commit is not None
    assert audit.config_precedes_results is True
    assert audit.is_verified is True
    lines = audit.lines()
    assert any("ordering verified: yes" in line for line in lines)
    assert any(audit.config_commit.short in line for line in lines)
    assert any(audit.results_commit.short in line for line in lines)


def test_a_configuration_committed_after_the_results_is_not_verified(repository: Path) -> None:
    """The failure the Sponsor asked for a mechanism against.

    The numbers land first and the specification is written, or widened, once they
    are known. Nothing inside the run can detect that. The history can, and the
    answer must be NO rather than a hedge.
    """
    commit_results(repository)
    commit_config(repository)
    audit = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    assert audit.results_commit is not None
    assert audit.config_precedes_results is False
    assert audit.is_verified is False
    assert any("ordering verified: NO" in line for line in audit.lines())


def test_the_audit_names_both_paths_and_both_instants(repository: Path) -> None:
    """A reader has to be able to check out both commits from the report alone."""
    commit_config(repository)
    commit_results(repository)
    audit = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    joined = chr(10).join(audit.lines())
    assert CONFIG.as_posix() in joined
    assert RESULTS.as_posix() in joined
    assert audit.config_commit.committed_at.isoformat() in joined
    assert audit.results_commit is not None
    assert audit.results_commit.committed_at.isoformat() in joined


def test_an_amended_configuration_still_precedes_results_committed_after_it(
    repository: Path,
) -> None:
    """An amendment committed before the run is still an ordering that holds.

    F1's specification carries three amendments, each committed before the grid
    ran. The audit must recognise the *latest* commit touching the file, because
    that is the version the run actually read.
    """
    commit_config(repository)
    write(repository, CONFIG, "version: v1.2\n")
    git(repository, "add", CONFIG.as_posix())
    git(repository, "commit", "-q", "-m", "docs: amendment 3, before the grid ran")
    amended = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    commit_results(repository)
    audit = ordering_audit(root=repository, config_path=CONFIG, results_path=RESULTS)
    assert amended.results_commit is None
    assert audit.config_commit.subject.startswith("docs: amendment 3")
    assert audit.is_verified is True


# ---------------------------------------------------------------------------
# The adapter's own primitives
# ---------------------------------------------------------------------------


def test_a_file_never_committed_has_no_commit_touching_it(repository: Path) -> None:
    """A genuine empty answer, distinct from the failures that raise."""
    git_repository = GitRepository(root=repository)
    assert git_repository.last_commit_touching(RESULTS) is None


def test_ancestry_is_directional(repository: Path) -> None:
    """``is_ancestor`` must not be symmetric, or the audit proves nothing."""
    commit_config(repository)
    first = GitRepository(root=repository).head()
    commit_results(repository)
    second = GitRepository(root=repository).head()
    git_repository = GitRepository(root=repository)
    assert git_repository.is_ancestor(first.sha, second.sha) is True
    assert git_repository.is_ancestor(second.sha, first.sha) is False


def test_a_commit_cite_carries_sha_instant_and_subject(repository: Path) -> None:
    commit_config(repository)
    commit = GitRepository(root=repository).head()
    cited = commit.cite()
    assert commit.short in cited
    assert commit.committed_at.isoformat() in cited
    assert commit.subject in cited
