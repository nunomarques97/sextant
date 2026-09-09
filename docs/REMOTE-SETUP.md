# Remote setup - two commands for the Sponsor

The private GitHub repository **was created** and the remote **is already
configured**:

- <https://github.com/nunomarques97/sextant> (private)
- `origin` -> `https://github.com/nunomarques97/sextant.git`

The first push was **rejected**, and no commits have reached GitHub yet. The
reason is a missing token scope, not a mistake in the repository:

```
! [remote rejected] HEAD -> main (refusing to allow an OAuth App to create or
  update workflow `.github/workflows/ci.yml` without `workflow` scope)
```

The `gh` CLI on this machine is authenticated with the scopes `gist`,
`read:org` and `repo`. Creating or updating a GitHub Actions workflow file
additionally requires the `workflow` scope. Granting it opens a browser, so it
cannot be done from an automated session.

## What to run

Open PowerShell in `C:\Users\User\Desktop\sextant` and run these two commands in
order.

**1. Grant the missing scope.** This opens a browser and asks you to authorise;
approve it.

```powershell
gh auth refresh -h github.com -s workflow
```

**2. Push.**

```powershell
git push -u origin main
```

That is all. Nothing else needs changing.

## Checking it worked

```powershell
git log origin/main --oneline -1
gh run list --limit 1
```

The first prints the newest commit now on GitHub. The second lists the CI run
that the push triggers; it runs ruff, mypy strict, the layering contracts and
the tests on Linux.

## If you would rather not grant the scope

The alternative is to keep the workflow file out of GitHub, which means no CI.
That is not recommended: the layering contract in `.importlinter` is the main
safety mechanism in this repository, and CI is what makes it non-optional.

---

## Separately: `uv` is not installed on this machine

Unrelated to GitHub, and needed before any `uv run ...` command works from a
normal shell. One command, in PowerShell:

```powershell
winget install --id=astral-sh.uv -e
```

Then close and reopen the terminal so `uv` is on the PATH, and check it with:

```powershell
uv --version
```
