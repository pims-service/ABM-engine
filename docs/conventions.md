# Conventions

Commit, branch and pull request rules for ABM-engine. Most of them are
enforced by [pre-commit](https://pre-commit.com) hooks (`.pre-commit-config.yaml`).

## Install the hooks

```bash
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Run every hook against the whole repo at any time:

```bash
pre-commit run --all-files
```

The `--hook-type commit-msg` part matters: without it the commit message
check is not installed.

### What the hooks do

| Hook | Scope | Notes |
| --- | --- | --- |
| trailing-whitespace, end-of-file-fixer, mixed-line-ending | all files | Auto-fixes |
| check-merge-conflict, check-added-large-files (1 MB), check-yaml, check-json | all files | |
| detect-secrets | all files | Blocks credentials; see below |
| ruff, ruff-format | `backend/` | Uses `backend/pyproject.toml` once issue #30 lands |
| prettier, eslint | `frontend/` | Local hooks; they skip with a message until `frontend/node_modules` has the tools (issue #31 plus `npm install`), then activate automatically |
| conventional-commit | commit message | See below |

### Secrets

Never commit real API keys, tokens, usernames, passwords or database
credentials. Use placeholders such as `YOUR_API_KEY` in code, docs and
examples, and keep real values in untracked `.env` files. If a false positive
is flagged, add an inline `# pragma: allowlist secret` comment. The full policy
(where real values live, rotation, CI) and the list of every variable are in
[environment.md](environment.md).

## Commit messages (Conventional Commits)

Format:

```
<type>(<optional scope>): <description>

<optional body>
```

Allowed types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`.
Add `!` before the colon for breaking changes (`feat(api)!: ...`).

Valid examples:

```
feat(backend): add lead scoring endpoint
fix: handle empty company name in importer
docs: describe branch naming
chore(deps): bump ruff
test(frontend): cover review queue filters
refactor(providers): extract retry helper
ci: run pre-commit in GitHub Actions
```

Rejected examples:

```
Updated stuff
feat add scoring
Fix: capitalised type
feat(backend):no space
```

Merge, `Revert "..."`, `fixup!` and `squash!` commits are let through.

## Branch naming

`<type>/<issue-number>-<short-slug>`, branched from `main`:

```
feat/33-pre-commit-hooks
fix/48-importer-crash
docs/12-api-overview
```

## Pull requests

- Title follows the same Conventional Commits format as a commit message.
- Link the issue in the description with `Closes #N` (or `Refs #N` if it only
  partly resolves it).
- Keep PRs focused on one issue; mention follow-ups instead of bundling them.
- Checklist before requesting review:
  - [ ] `pre-commit run --all-files` passes
  - [ ] Tests added or updated, and passing
  - [ ] Docs updated where behaviour changed
  - [ ] No secrets or real credentials in the diff
  - [ ] PR title is a valid Conventional Commit and links the issue
