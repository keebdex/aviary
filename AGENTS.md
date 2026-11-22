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
    client, and `revision_source(job, origin)` for its source (see "Revision
    sources"); `fetch_all(client, table, columns, where, order)` for every paged
    read (also on `client.schema("ingest")`); `PUBLISHED_FILTER` (Keebdex's
    published rule); `Row`.
  - `pool.py`: `run_each(items, work)`, network work a few at a time
    (`CONCURRENCY`), counting failures.
  - `http.py`: `create_http_client()` for reading websites.
  - `report.py`: `configure_logging()` and `write_summary(markdown)` (GitHub
    Actions job summary).
  - `slug.py`: `url_slugify`, python-slugify with Keebdex's punctuation rules,
    plus `LEGACY_SLUGS`, the slugs catalog-sync gave names python-slugify slugs
    differently (`docs/slugs.md`).
  - `ids.py`: `crc32_hex`, the colorway ids catalog-sync made with npm `crc`.
  - `cf_images.py`: Cloudflare Images (`list_images`, `upload_image` and
    `upload_file`, with catalog-sync's fallbacks: download and send as a file
    what Cloudflare cannot fetch, downscale what is too large, refuse deleted
    imgur images; `download_original`, `delete_image`). Only `pelican prune`
    deletes images.
  - `r2.py`: `Bucket`, the R2 backup bucket over its S3 API (boto3), and its
    lock rules through the Cloudflare API.
- `src/aviary/lorikeet/`: reference color palettes. `ral.py` crawls
  ralcolorchart.com, `sync.py` plans and writes the `colors` rows, `__main__.py`
  is the `lorikeet` command.
- `src/aviary/bowerbird/`: the artisan sync, ported from `../catalog-sync`.
  `gdoc/` holds the Google Docs side (`parser.py`, `dates.py`, `jsregex.py`,
  `google.py` for the read-only API calls, `importer.py`); `jsregex.py` also
  holds JavaScript's whitespace (`JS_WHITESPACE`, `js_trim`), and per-maker name
  markers are data in `parser.MAKER_MARKERS`, not branches. `makers/` holds one
  scraper per maker (`SCRAPERS`, `PARTIAL_MAKERS`), `plan.py` the pure port of
  catalog-sync's `updateMakerDatabase`, `apply.py` the reads and writes,
  `images.py` the colorway image ids and uploads, `__main__.py` the `bowerbird`
  command. See "Artisan sync".
- `src/aviary/pelican/`: Cloudflare images and their R2 backups, ported from
  catalog-sync's image jobs. `references.py` (`IMAGE_COLUMNS`, which images the
  database references), `sync.py`, `maintenance.py` (restore, prune, backup),
  `__main__.py` the `pelican` command. See "Images and backups".
- `src/aviary/magpie/`: the Geekhack crawler, ported from huginn without its
  web UI. `geekhack.py` (boards, topic URLs, `normalize_image_url`), `fetch.py`
  (the polite `Fetcher`), `parsers.py` (board list and first post), `kits.py`,
  `classify.py` (title to kind and profile code), `proposal.py` (thread to
  keyset candidate, `EXTRACTOR_VERSION`), `plan.py` (listings, fetch schedule,
  promotions, pure), `apply.py` (the `ingest` tables and the Keebdex writes),
  `__main__.py` the `magpie` command.
  See "Forum crawl".

## Commands

- `uv sync`: install. `uv run pre-commit install` once per clone (ruff and the
  Conventional Commits check on commit messages).
- `uv run ruff check .` and `uv run ruff format .`: lint and format. CI
  (`owl`, `.github/workflows/owl.yml`) runs both.
- `uv run pytest`: tests (CI runs them too). Do not claim tests passed unless
  they were actually run.
- `uv run --env-file .env lorikeet ral [--dry-run | --csv PATH]`.
- `uv run --env-file .env bowerbird gdoc|scrape [--maker ID] [--dry-run]`.
- `uv run --env-file .env pelican sync|restore|prune|backup [--dry-run]`
  (`restore --force`, `backup --limit N --force-trash`).
- `uv run --env-file .env magpie crawl [--board B] [--max-pages N]
  [--fetch-limit N] [--dry-run]`, `magpie promote [--limit N]
  [--max-age-days N] [--dry-run]`; `uv run magpie topic <id|url>` (no
  database).

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
- Manual runs (`workflow_dispatch`) default to a dry run. Scheduled runs write
  (lorikeet's schedule is paused). bowerbird and pelican run at catalog-sync's
  old times: bowerbird at 23:00 UTC, pelican sync and restore at 01:00, backup
  and the database dump at 02:00, prune at 00:00 on the 1st and the 15th.
  magpie has no schedule until its schema is applied.

## Revision sources

Keebdex records inserts, updates, and deletes on its catalog tables (sculpts,
colorways, keysets, kits, keyboards, releases, variants) as revisions. With the
service role key it trusts two request headers to tell which job made a change:
`x-keebdex-source` and `x-keebdex-run` (`GITHUB_RUN_ID` in CI, otherwise a UUID
per process). `create_supabase(source)` sends them when `source` is given.

- Pass a source only for jobs that write revisioned tables, built with
  `revision_source(job, origin)`: `<job>.<origin>`, the command that writes and
  the site or document it read, both lowercase slugs. Keebdex accepts any value
  of that shape (`is_sync_revision_source()`), so a new job or site needs no
  migration; `revision_source()` raises on any other, which Keebdex would
  silently record as `sql`.
- `lorikeet` writes only `colors`, which has no revisions, and sends no source.
- `bowerbird gdoc` sends `bowerbird.gdoc`; `bowerbird scrape` gives each maker
  a client of its own sending `bowerbird.<maker_id>`; `pelican sync` sends
  `pelican.cloudflare-images`. These are not the `source` column of the artisan
  rows (`gdoc`, `scraper`), which says which sync owns a row.
- `magpie promote` sends `magpie.geekhack`, the forum its candidates come from
  (a vendor store would be `magpie.novelkeys`); `magpie crawl` writes only
  `ingest` and sends none.

## Artisan sync

`bowerbird` is the artisan sync that used to live in catalog-sync
(`gdocs-importer.js`, `maker-scraper.js`, `artisans/utils/database.js`,
`parser.js`). Its output was checked against the JavaScript on real data: the
parser on every maker's doc, the scrapers on the live sites, and the sync on
1,000 random scenarios. These rules came with it.

### Input

Every source (each maker scraper, the Google Docs parser) produces the same
input: sculpt dicts shaped like `artisan_sculpts` rows, each with a `colorways`
list shaped like `artisan_colorways` rows, and `remote_img` (the source image)
on colorways. An absent key means "not provided" and never syncs; a key set to
None clears the stored value. Keep that distinction when porting or writing a
source: it is JavaScript's `undefined` versus `null`.

### Ownership

- Sculpts and colorways have a `source` column: `gdoc` (`bowerbird gdoc`),
  `scraper` (`bowerbird scrape`), `keebdex` (added in the app). A sync only
  reads, matches, updates, or removes rows of its own source, so the doc sync
  and the website sync of one maker never delete each other's rows.
- Updates patch only `SYNCABLE_FIELDS` / `SCULPT_SYNCABLE_FIELDS`, skipping
  fields in the row's `overridden_fields` (locked in the app).
  `ALWAYS_SYNCED_FIELDS` / `SCULPT_ALWAYS_SYNCED_FIELDS` (`order`,
  `source_document_id`) are metadata the app cannot edit, so they sync
  regardless; only add a field there if the app can't edit it. Lists (`stem`)
  compare by sorted content, and a missing list equals an empty one.
- Synced rows are trusted maker data: inserted with `review_status` null
  (published), never touching moderation columns, and with `submitted_by` null
  (Keebdex sends no notifications about them).

### Matching and removal

1. A colorway matches a stored row by `colorway_id` first.
2. Otherwise by name, but only against orphaned stored rows (whose `colorway_id`
   matches no incoming row), each claimable once, from per-name queues:
   duplicate or empty names must not collapse into one candidate. A name match
   also syncs the new `colorway_id` (unless `img` is locked).
3. Stored colorways nothing claimed are deleted, or marked `deleted` while a
   user collection holds them.
4. A stored sculpt nothing matched (gone from the source, or renamed there) is
   removed after its colorways: deleted when no colorway of any source points at
   it, marked `deleted` when only deleted ones do, and kept while colorways of
   other sources do. Never delete colorways by `maker_sculpt_id`.
5. A sculpt renamed in Keebdex keeps its source slug in `source_slug`, and
   incoming rows are pointed at the renamed slug.

### Partial syncs

- A maker can have several docs (`document_ids`), and one sculpt can span them:
  `merge_documents` (catalog-sync's `deepmerge` with `customMerge`) takes the
  later doc's fields and appends its colorways with their `order` shifted past
  the earlier ones (+ length + 1).
- Docs download one by one. A 404 skips that doc and makes the sync partial;
  every doc gone (or a 404 anywhere else in the maker) disables the maker's sync
  and marks it deleted. Any other error fails the maker.
- Every parsed row carries `source_document_id`. With `preserve_missing`, a
  stored row absent from the input is kept unless its `source_document_id` is
  one of the docs that loaded (`available_document_ids`); a null one is kept.
- In a partial sync existing colorways keep their stored `order`; new ones get
  the sculpt's stored count plus their index in the input.
- `PARTIAL_MAKERS` (keycat) are always partial: their sources hold only part of
  their catalog. The keycat scraper stops at Pink Camo, where its doc takes
  over.

### Ids, images, and failures

- Colorway ids, image ids, and sculpt slugs must stay what catalog-sync made
  (`url_slugify`, `crc32_hex`, `images.image_id`, the doc's image object ids),
  or rows would be replaced. `url_slugify` is python-slugify, so for names
  catalog-sync slugged differently `LEGACY_SLUGS` keeps the old slug
  (`docs/slugs.md`); new names follow python-slugify. Recheck the map against
  the database before bowerbird takes over.
- The parser is a line-by-line port, quirks included (a regex's left-over
  `lastIndex`, first-match-only replaces). Check a parser change against the
  previous output on real docs. Release dates are the exception: `dates.py`
  converts only "January 2023" / "Jan 2023" and "2023/5/17" with `strptime` and
  keeps anything else as written (with a warning, once per value and run), where
  catalog-sync's date-fns also took a one-letter month or a 1 to 3 digit year.
  Misspelled months found in docs go in `MONTH_TYPOS` ("Sept", "Augus") and
  words between month and year in `RELEASE_NOISE` ("Exclusive"); catalog-sync
  kept those values as written, so adding one rewrites them on the next sync.
- A colorway's `img` is its Cloudflare URL (`artisan/<maker>/<sculpt>/<id>`),
  set before upload; the image keeps that path after a sculpt rename. Images are
  never deleted here (`pelican prune` removes unused ones), and a failed upload
  is logged and retried on the next run.
- A maker that fails is logged and skipped, and the run still exits 0: a deleted
  or broken doc is routine and must not stop the other makers or the next
  workflow step. Failed makers are listed at the end of the log. A source that
  returns nothing changes nothing.
- The job summary (`summary()` in `bowerbird/__main__.py`) has a row per maker
  whose sync changed something (rows added, updated, removed, images uploaded;
  in a dry run, what would change), a total, the count of makers with no
  changes, and the makers that failed or were disabled. Writes that change
  nothing log nothing, so a quiet maker shows only its "Syncing" line.
- When colorways were added or changed, the maker's `contributors` and
  `updated_at` are refreshed from the last doc's Drive revisions.

### Known caveats

- Rows are scoped by `source`, so a colorway added in the app and the same
  colorway later appearing in a doc are never reconciled: the doc's copy is
  inserted as a new row (rare duplicates).
- A sculpt another source already holds (keycat's sculpts are in both its doc
  and on its website) is not inserted again: the incoming colorways go under
  it, and the sculpt stays that source's, which alone updates or removes it.
  catalog-sync inserted it and, once insert errors stopped being ignored,
  failed the whole maker on the primary key.
- In a partial sync the index used for a new colorway's `order` is global across
  all incoming colorways, not per sculpt. Gaps are harmless: only the relative
  order matters.

## Forum crawl

`magpie` is huginn (a crawler with a web UI for reviewing Geekhack topics by
hand) as a batch job. Its parsers were checked against huginn's on saved and
live pages; the choices huginn's UI left to a person are now made from the
topic, and staff review the result in Keebdex instead.

- Its data lives in the `ingest` schema of supabase migration
  `20261119000000_ingest_schema.sql` (read its header before changing what
  magpie writes): `threads` (listing, first post, fetch queue), `candidates`
  (extractions waiting for triage), `crawl_runs`. Staff read and triage them
  from the app under RLS; magpie uses the service role. The schema must be
  exposed through the Data API.
- `crawl` is one `crawl_runs` row (one running at a time; a run running for
  over an hour is marked failed). Each board is listed down to the watermark
  of the last succeeded run (the newest last post it saw), its last page, or
  `--max-pages`; a board the cap stopped is in `incomplete_boards` and keeps
  its old watermark. Every board is read before anything is written, and only
  a succeeded run moves watermarks. Stickies are skipped. Geekhack times have
  no zone and are stored as UTC.
- A listed thread is due (`next_fetch_at`) when new or when its last post
  moved, gone ones included; otherwise the listing only refreshes it. A fetch
  stores the `div.post` HTML (`raw_body`, `parsers.parse_post` reads it again)
  and its hash (`changed_at` when it changes), then schedules the next fetch:
  weekly while the thread is under 90 days old (edits don't bump a thread),
  else on new activity only. A failure backs off from an hour, doubling, and
  stops after 5 in a row until new activity starts the count over; a 404 sets
  `gone_at`. Failures are routine and the run still succeeds. robots.txt is
  retried like a page: a 4xx means no rules, unreachable fails the run.
- Each fetched keyset thread (by title, `classify.py`) gets a candidate:
  `payload` shaped like the keyset submission (`keyset` columns, `kits`),
  `confidence` per field, `match_key` when Keebdex has the keyset. The
  `candidates_supersede` trigger drops an extraction already made (same
  `EXTRACTOR_VERSION` and content) and supersedes the waiting one; when the
  first post goes back to an earlier version it puts that superseded
  extraction back to `new` (the summary counts these apart, from the state
  read before the insert). Bump the version when `extract()` changes.
  Keyboards are not extracted yet.
- Extraction: the title's keycap code must name exactly one published profile
  (`profile_id`, then name: KKB is `keykobo`); a second code ("GMK MTNU
  Dasher") picks that profile. The name drops the codes and the profile's name
  ("GMK CYL Selene" is `gmk/selene`), the slug is `url_slugify`. Status is
  Interest Check on the IC board, Cancelled when the title says so, else
  Confirmed; `ic_date` the IC's posting day; Cherry profiles get sculpt
  `1-1-2-3-4-4`. Kits: one per labelled kit image (`kits.py`), else a Base kit
  with the cover (the first image when no Base label).
- `promote` inserts, newest topic first and at most `--limit`, the waiting
  candidates with no `match_key` whose payload is complete
  (`missing_for_promotion`) and whose thread was posted within
  `--max-age-days` and is neither ignored nor gone: the keyset and its kits
  `Pending` with a null `submitted_by`, like catalog-sync's vendor keysets,
  then the candidate `promoted` (`promoted_kind` keyset). Keebdex's keyset
  queue lists kits, so when the kits fail, or staff triaged the candidate
  meanwhile (the claim finds it no longer `new`; listed apart in the
  summary, not a failure), the keyset just inserted and its kits are deleted
  again. A claim that raises is read back first: the keyset stays when the
  candidate is promoted to it or cannot be read, so no candidate points at a
  deleted keyset. A failed delete is logged with the id for removal by hand
  and the run goes on. A candidate whose keyset Keebdex has now (its topic's
  URL in any `topic=` form, or its key, of any source or review status, or a
  keyset this run inserts) gets that `match_key` and waits for staff as an
  edit; keysets are never updated, a different status is only reported.
  Everything else waits for staff.
- An empty read of keysets or profiles writes nothing; a failed promotion
  makes `promote` exit 1. Images stay external: `pelican sync` uploads them
  once staff approve.

## Images and backups

Image columns: `artisan_colorways.img`, `keysets.img` / `order_graph` /
`order_history`, `keyset_kits.img`, `keyboard_variants.img_front` / `img_back`.
A Cloudflare image is stored as `<delivery base>/<id>/public` (ids may contain
slashes); anything else is an external URL, which Keebdex shows as it is.
`cf_images.delivery_url` builds that URL and `cf_images.to_image_id` reads it
back. `pelican/references.py` lists the columns in `IMAGE_COLUMNS`, which `sync`
uploads into too (a test checks it): add a column there whenever a table starts
storing Cloudflare images, or `prune` deletes its images.

- `pelican sync` (`pelican.cloudflare-images` revision source) uploads external
  images of published rows (the row and its keyset, keyboard, or sculpt null or
  Approved) under readable ids ending in a CRC32 of the source URL:
  `keyset/<profile_keyset_id>/<hash>` (cover), `.../order-graph/<hash>`,
  `.../order-history/<hash>`, `keyset/<pk>/kit/<id>/<kit_id or slug>/<hash>`,
  `keyboard/<brand_keyboard_slug>/<release_id>/<variant slug>/<side>/<hash>`,
  `artisan/<maker>/<sculpt>/<colorway_id>/<hash>` (colorways added in the app;
  bowerbird uploads its own). The hash gives a replaced image a new id:
  Cloudflare rejects an existing id, and an id must never change content or the
  R2 backup keeps the old file. Rows in review keep their original URL. A source
  answering 404 (or a deleted imgur image) clears the row's image (kits to
  `''`), except on keyboard variants.
- `pelican prune` deletes Cloudflare images no row references, counting rows of
  every review status and image URLs in the `changes` of Pending
  `edit_suggestions`, and skips images uploaded in the last 7 days (an app
  upload isn't referenced until its form is saved). Every read must succeed and
  at least one image must be referenced, or it deletes nothing.
- `pelican restore` re-uploads, from R2 (`images/<id>`) and under the same id,
  referenced images Cloudflare no longer has: a record pointing at a pruned
  image again after an Undo, a Restore, or an approved suggestion. It refuses
  when Cloudflare lists nothing or more than 10% of referenced images are
  missing (a bad listing), unless `--force`. Keebdex's revision history relies
  on this instead of keeping pruned images.
- `pelican backup` copies every Cloudflare image not yet in R2 to `images/<id>`
  through the API (`/blob`, with `CF_IMAGES_READ_TOKEN` when set: account tokens
  get a 403 on the app's UUID-named uploads), then moves backups of images gone
  from Cloudflare to `trash/images/<id>` once no enabled bucket lock rule
  covering the key retains them (`Age`, `Date`; any other condition, or a rule
  with a null prefix, retains). Rules that can't be read count as a lock on
  everything. It refuses to trash more than 10% of the backups in one run
  (`--force-trash` lifts it) or anything when Cloudflare lists nothing. A move
  is copy then delete, so a failure leaves the backup in both places, never in
  neither.
- The workflow's `database` job dumps the database (`pg_dump`, custom format) to
  `db/daily/<date>.dump`, and on the 1st also `db/monthly/<yyyy-mm>.dump`.
- Retention and protection are set on the bucket, not in code: a lifecycle rule
  expires `db/daily/` after 30 days, and `images/` is locked indefinitely (so
  nothing moves to the trash today). Decide how long Keebdex keeps revisions
  together with any finite lock on `images/`: an image pruned and trashed past
  it can no longer be restored.
- `docs/backups.md` walks an operator through the trash (turning it on is
  infrastructure only) and manual restores; keep it in step with `backup`.
- Unlike bowerbird, these jobs exit 1 when something failed or looked wrong; a
  failed prune deletion only logs.

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
scope is the module or area (`lorikeet`, `bowerbird`, `pelican`, `common`,
`ci`). Do not create commits, branches, or pull requests unless asked.
