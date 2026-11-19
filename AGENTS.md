# aviary Agent Instructions

This is the canonical instruction file for every AI coding agent working in this
repository (Claude Code, OpenAI Codex, Cursor, GitHub Copilot, and others).
`CLAUDE.md` imports it and `.github/copilot-instructions.md` points here. Update
this file instead of creating agent-specific copies; a tool-specific file only
points here and holds what is specific to that tool.

## What this repo is

Python jobs that collect data for [Keebdex](https://keebdex.org) and write it
into its Supabase (Postgres) database. Each job is a module named after a bird
under `src/aviary/`, run from the command line or on a schedule from
`.github/workflows/`. There is no web server.

The Keebdex app lives in the sibling repository `../keebdex`. Its `AGENTS.md`
describes the schema, the review workflow, and the server contract these jobs
must stay compatible with. Its SQL migrations live in `../keebdex/supabase` (a
separate repository).

## Layout

- `src/aviary/common/`: code every job shares. Add to it only when a job needs
  it.
  - `config.py`: `require_env(name)` for settings.
  - `db.py`: `create_supabase(source=None)`, the only way to create a Supabase
    client (see "Revision sources").
  - `http.py`: `create_http_client()` for reading websites.
  - `report.py`: `configure_logging()` and `write_summary(markdown)` (GitHub
    Actions job summary).
- `src/aviary/lorikeet/`: reference color palettes. `ral.py` crawls
  ralcolorchart.com, `sync.py` plans and writes the `colors` rows, `__main__.py`
  is the `lorikeet` command.
- Planned, not yet here: `magpie` (forum crawler writing to the `ingest` schema
  for staff review) and `bowerbird` (the artisan sync ported from
  `../catalog-sync`). Create a module only when its first real code lands.

## Commands

- `uv sync`: install. `uv run pre-commit install` once per clone (ruff and the
  Conventional Commits check on commit messages).
- `uv run ruff check .` and `uv run ruff format .`: lint and format. CI runs
  both (`.github/workflows/ci.yml`).
- `uv run --env-file .env lorikeet ral [--dry-run | --csv PATH]`.
- There is no test suite yet. Do not claim tests passed unless a relevant check
  was actually run.

## Rules for jobs

- Every job that writes takes `--dry-run`: it still reads the database, and logs
  what it would write instead of writing.
- A job checks that what it read looks complete before writing (every page
  loaded, counts close to what is stored) and writes nothing otherwise. A
  partial crawl must never delete or overwrite data.
- Do not overwrite values staff may have edited in Keebdex. Report differences
  instead (log and `write_summary`).
- Never delete rows other tables point at.
- Read the database in pages (`.range()`); PostgREST returns at most 1,000 rows
  per request.
- Keep requests to other websites polite: one at a time, with a delay between
  pages.
- Scheduled workflows run without `--dry-run`; manual runs (`workflow_dispatch`)
  default to a dry run.

## Revision sources

Keebdex records inserts, updates, and deletes on its catalog tables (sculpts,
colorways, keysets, kits, keyboards, releases, variants) as revisions. With the
service role key it trusts two request headers to tell which job made a change:
`x-keebdex-source` and `x-keebdex-run` (`GITHUB_RUN_ID` in CI, otherwise a UUID
per process). `create_supabase(source)` sends them when `source` is given.

- Pass a source only for jobs that write revisioned tables. A new source must
  also be whitelisted in Keebdex's `record_revision()` trigger and the
  `record_revisions_source_check` constraint (a migration in
  `../keebdex/supabase`).
- `lorikeet` writes only `colors`, which has no revisions, and sends no source.

## Code style

- Python 3.13, typed, formatted and linted by ruff (`pyproject.toml`). Use
  `logging` (module-level `log = logging.getLogger(__name__)`), never `print`.
- Keep crawling (fetch and parse) apart from syncing (compare and write), so a
  crawl can run without a database (`--csv`).

## Git

Commit messages follow
[Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>): <summary>`, imperative and lowercase, no trailing period. The
scope is the module or area (`lorikeet`, `common`, `ci`). Do not create commits,
branches, or pull requests unless asked.
