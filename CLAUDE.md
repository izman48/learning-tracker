# CLAUDE.md

Guidance for working in this repo. Keep it current when commands or conventions change.

## What this is

learning-tracker: an open-source, self-hostable, multi-user phone PWA that builds each user a 3-item morning reading list for topics they chose. It shows original links, "why relevant" and an HN gist, and no AI summaries. Licence: AGPL-3.0.

## This repo is public

Everything here is public from the first push. That includes code, tests, fixtures, comments, commit messages, PR bodies and review comments. Editing does not help: GitHub keeps prior versions of edited PR text, and old commits stay reachable.

- Never write a personal domain, server name, IP address, email address, or anyone's family or company. Use `example.com` / `example.org` only. `tests/repo/test_public_text.py` enforces this for email addresses.
- Never commit secrets or a real `.env`. `.env.example` holds `<placeholders>` only (`tests/repo/test_env_example.py`).
- A "must not mention X" test must not contain X. Pin approved text by exact equality instead.

## Layout

```
src/learning_tracker/
  db/         SQLAlchemy models and Alembic migrations
  web/        FastAPI app (API + PWA files). Imports no outbound HTTP client
              (tests/web/test_web_imports.py).
  worker/     nightly pipeline, mail, model calls (later tickets)
  fetcher/    SSRF-safe page fetcher, its own container (later tickets)
tools/        repo tooling (workflow checker)
tests/        mirrors src/; tests/repo/ holds repo-level checks
```

## Commands

```bash
uv sync --locked          # install exactly what uv.lock says
uv run pytest             # full suite (needs TEST_DATABASE_URL, see below)
uv run ruff check .       # lint
uv run ruff format .      # format (CI runs --check)
uv run mypy               # strict type check
uv run python -m tools.check_workflows   # public-CI rules for .github/workflows
```

**Database tests** run against real Postgres, never SQLite, so foreign keys and constraints behave as in production. Each test gets a fresh database that is dropped afterwards. Run a throwaway server and point the tests at it. The variable deliberately has no `LT_` prefix, because the app refuses unknown `LT_*` settings:

```bash
docker run -d --name lt-test-pg -e POSTGRES_PASSWORD=devonly -p 127.0.0.1:55432:5432 postgres:18.6-alpine
export TEST_DATABASE_URL=postgresql+psycopg://postgres:devonly@127.0.0.1:55432/postgres
```

**Migrations** live in `src/learning_tracker/db/migrations/versions/`:
- Each one has a working downgrade.
- `tests/db/test_migrations.py` checks that the migrated schema equals the models, and that downgrade to base removes everything.
- Any new foreign key to `users.id` must cascade, or be listed in `tests/db/test_user_data_cascade.py`.

CI (`.github/workflows/ci.yml`) runs the same commands, the workflow checker and gitleaks over the full history. The `required` job is the single status to look at; every new job must be in its `needs` (the checker enforces it). `codeql.yml` runs on PRs and weekly; `audit.yml` runs pip-audit weekly, on demand and on `main`.

## Conventions

- **Test-first.** Write the failing test, watch it fail for the right reason, then write the code.
- **Security behaviour has tests,** and every negative assertion also asserts its precondition: the control is present, or the detector fires on a known-bad sample.
- **Dependencies:** pin exact versions in `pyproject.toml`; `uv.lock` is committed.
  - Prefer releases that are at least 14 days old.
  - Check each new package on its registry page.
  - Justify each new package in the PR body.
- **GitHub Actions** (enforced by `tools/check_workflows.py`):
  - Pin every action by full commit SHA, with the tag in a comment. Docker images are pinned by digest.
  - Use `permissions: contents: read` and `persist-credentials: false`.
  - Never use `pull_request_target` or `workflow_run`.
  - No cache writes in workflows that PRs trigger.
  - `npm ci` always gets `--ignore-scripts`.
- **Config** comes from `LT_*` environment variables only. A missing required value stops startup.
- **The architecture decision record** lives outside this repo. Behaviour that matters is restated in code comments and tests, not referenced by ticket id alone.
