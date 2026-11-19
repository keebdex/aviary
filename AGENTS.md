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
  - `slug.py`: `url_slugify`, a port of catalog-sync's and Keebdex's
    `urlSlugify` (npm `slugify` 1.6.9 and its `slugify_charmap.json`). Page keys
    are built from slugs, so it must match the JavaScript output exactly:
    `tests/test_slug.py` checks it against values taken from catalog-sync. No
    Python library does: npm `slugify` drops every character missing from its
    charmap (CJK, `×`, `²`) where they transliterate, so `python-slugify` would
    change 29 of the 3,685 sculpt slugs catalog-sync has made.
  - `ids.py`: `crc32_hex`, the colorway ids catalog-sync made with npm `crc`.
  - `cf_images.py`: Cloudflare Images (`list_image_ids`, `upload_image`, with
    catalog-sync's fallbacks: download and send as a file what Cloudflare cannot
    fetch, downscale what is too large, refuse deleted imgur images). It never
    deletes images.
- `src/aviary/lorikeet/`: reference color palettes. `ral.py` crawls
  ralcolorchart.com, `sync.py` plans and writes the `colors` rows, `__main__.py`
  is the `lorikeet` command.
- `src/aviary/bowerbird/`: the artisan sync, ported from `../catalog-sync`
  (maker websites so far; the Google Docs importer is next). `makers/` holds one
  scraper per maker (`SCRAPERS`, `PARTIAL_MAKERS`), `plan.py` the pure port of
  catalog-sync's `updateMakerDatabase`, `apply.py` the reads and writes,
  `images.py` the colorway image ids and uploads, `__main__.py` the `bowerbird`
  command. See "Artisan sync".
- Planned, not yet here: `magpie` (forum crawler writing to the `ingest` schema
  for staff review). Create a module only when its first real code lands.

## Commands

- `uv sync`: install. `uv run pre-commit install` once per clone (ruff and the
  Conventional Commits check on commit messages).
- `uv run ruff check .` and `uv run ruff format .`: lint and format. CI runs
  both (`.github/workflows/ci.yml`).
- `uv run pytest`: tests (CI runs them too). Do not claim tests passed unless
  they were actually run.
- `uv run --env-file .env lorikeet ral [--dry-run | --csv PATH]`.
- `uv run --env-file .env bowerbird scrape [--maker ID] [--dry-run]`.

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
- `bowerbird scrape` sends `scraper`, the source catalog-sync's maker scraper
  used, so Keebdex shows both the same way.

## Artisan sync

`bowerbird` must behave exactly like catalog-sync's artisan sync while both
exist, since they write the same rows. catalog-sync's `AGENTS.md` ("Artisan data
ownership", "Colorway matching rules", "Removed sculpts", "Partial syncs",
"Failures") explains the rules; `plan.py` follows them line by line.

- Every source (each maker scraper, later the Google Docs importer) produces the
  same input: sculpt dicts shaped like `artisan_sculpts` rows, each with a
  `colorways` list shaped like `artisan_colorways` rows. An absent key means
  "not provided" and never syncs; a key set to None clears the stored value.
  Keep that distinction when porting a source: it is JavaScript's `undefined`
  versus `null`.
- Only rows of the sync's own `source` are read, matched, or removed. Only the
  fields in `SYNCABLE_FIELDS` / `SCULPT_SYNCABLE_FIELDS` sync, skipping those in
  the row's `overridden_fields`; `ALWAYS_SYNCED_FIELDS` sync regardless.
- Removed colorways are deleted, or marked `deleted` while a user collection
  holds them. A removed sculpt is deleted only when no colorway of any source
  points at it, marked `deleted` when only deleted ones do, and kept while
  colorways of other sources do.
- Colorway ids, image ids, and sculpt slugs must stay what catalog-sync makes
  (`crc32_hex`, `url_slugify`, `images.image_id`), or every row would be
  replaced. A scraper port is checked by comparing its output with the
  JavaScript scraper's on the live site.
- Images are never deleted here (catalog-sync's `prune-images` does it), and a
  failed upload is logged, not fatal: its id is still missing, so the next run
  retries it.
- A maker that fails (scrape or database error) is logged and skipped, and the
  run exits non-zero. A scraper that returns nothing changes nothing.

## Code style

- Python 3.13, typed, formatted and linted by ruff (`pyproject.toml`). Use
  `logging` (module-level `log = logging.getLogger(__name__)`), never `print`.
- Keep crawling (fetch and parse) apart from syncing (compare and write), so a
  crawl can run without a database (`--csv`) and the sync logic can be tested
  without one (`bowerbird/plan.py`, `tests/test_plan.py`).

## Git

Commit messages follow
[Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>): <summary>`, imperative and lowercase, no trailing period. The
scope is the module or area (`lorikeet`, `bowerbird`, `common`, `ci`). Do not
create commits, branches, or pull requests unless asked.
