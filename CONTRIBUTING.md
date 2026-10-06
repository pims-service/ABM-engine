# Contributing

Thanks for helping out. This guide keeps changes easy to review.

## Ground rules

- Work from an issue. If there is none, open one first.
- Keep changes small and focused on one issue.
- Check the README's out-of-scope list before adding features.
- **Never commit secrets.** No API keys, tokens, passwords, usernames or database credentials, in code, config, docs, tests or commit history. Use placeholders such as `YOUR_API_KEY` or `********`, and document variables in `.env.example`. If a secret is committed by mistake, tell the team right away so it can be rotated; deleting it in a later commit is not enough.

## Branch naming

Branch from `main` and use `<type>/<issue-number>-<short-description>`:

```
feat/20-monorepo-structure
fix/57-csv-import-encoding
docs/31-adr-provider-adapters
chore/24-pre-commit-hooks
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`.

## Commit style

Use short, imperative messages with a type prefix:

```
docs: add contribution guide
chore: add root gitignore and editorconfig
feat(backend): add health endpoint
```

- Subject line under about 72 characters, no trailing period.
- Explain the why in the body when it is not obvious.
- Make logical commits; avoid one giant commit and avoid "fix typo" chains (squash them locally).

## Pull requests

1. Push your branch and open a PR against `main`.
2. Fill in the PR template and link the issue (`Closes #N`).
3. Keep the PR to one issue. Mark it as draft until it is ready.

PR checklist (also in the template):

- [ ] Linked issue and clear description
- [ ] Scope matches the issue (nothing from the out-of-scope list)
- [ ] Tests added or updated, and they pass
- [ ] Lint and type checks pass
- [ ] Docs updated where behavior or setup changed
- [ ] No secrets, real keys or personal data committed
- [ ] `.env.example` updated if you added a variable

## Review expectations

- At least one approval from someone other than the author before merging.
- Reviewers: respond within a working day or two, be specific, and separate must-fix comments from suggestions.
- Authors: reply to every comment, push fixes as new commits during review, and do not merge with unresolved discussions.
- Check that AI-driven logic uses structured output and that evidence (source and date) is kept, not fabricated.

## Issues

Use the issue templates under `.github/ISSUE_TEMPLATE/` (bug or task).

## Code style

Editor basics are set in `.editorconfig`. Language-specific linting and formatting for backend and frontend will be documented here once those setups are merged.
