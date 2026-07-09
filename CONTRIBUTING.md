# Contributing

This project should use one local repository and one working copy.

The goal is:

- GitHub Desktop and Codex point at the same folder.
- Codex commits appear in GitHub Desktop immediately.
- No duplicate clones.
- No manual copying between workspaces.
- No live trading, brokerage login, or auto-ordering features.

## Local Repository Setup

Use GitHub Desktop as the owner of the local checkout.

Recommended local path:

```text
~/Documents/GitHub/OptionsEngine
```

If the repository already exists somewhere else, keep one copy and delete or archive duplicate clones after confirming all useful commits have been pushed or copied with Git.

## Codex Setup

Open the same local folder in Codex that GitHub Desktop uses.

Do not start Codex work from a generated workspace clone such as:

```text
~/Documents/Codex/.../work/OptionsEngine
```

For the one-working-copy workflow, Codex should operate on:

```text
~/Documents/GitHub/OptionsEngine
```

or whatever exact folder GitHub Desktop shows under Repository > Show in Finder.

## Daily Workflow

1. Open the repository in GitHub Desktop.
2. Confirm the current branch.
3. Open the same repository folder in Codex.
4. Ask Codex to create a feature branch before making code changes.
5. Let Codex edit, test, and commit on that branch.
6. Review the commit in GitHub Desktop.
7. Push from GitHub Desktop or from an authenticated terminal.
8. Open a pull request when ready.

## Branches

Use feature branches for all implementation work.

Examples:

```text
feature/phase-1-two-sided-engine
feature/phase-2-premium-efficiency
feature/phase-3-preference-engine
feature/phase-4-portfolio-intelligence
feature/dashboard-polish
feature/earnings-filter
```

Avoid committing directly to `main`.

## Codex Worktrees

Codex worktrees are useful for parallel experiments, but they create another checkout.

For this project, prefer Codex Local mode against the GitHub Desktop checkout. Use a Codex worktree only when you intentionally want a separate temporary checkout.

If Codex creates work in a worktree, hand the thread off to Local before continuing day-to-day development.

## Tests

Before committing, run:

```bash
.venv/bin/python -m pytest -q
```

If the virtual environment does not exist yet:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

## Safety Boundaries

This app is decision support only.

Do not add:

- live trading
- brokerage login
- order placement
- order staging
- credential storage
- Merrill scraping

Market-data API keys should stay in `.env` or Streamlit secrets and should never be committed.

## Commit Checklist

Before a commit:

- Confirm you are on a feature branch.
- Run the tests.
- Review changed files.
- Keep the change scoped to the requested phase or feature.
- Update README/tests when behavior changes.

