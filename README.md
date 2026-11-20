# aviary

The data jobs behind [Keebdex](https://keebdex.org), each a module named after a
bird:

| Module      | What it does                                   | Status  |
| ----------- | ---------------------------------------------- | ------- |
| `lorikeet`  | Adds reference color palettes (RAL) to Keebdex | Ready   |
| `bowerbird` | Syncs artisan catalogs from makers and Docs    | Porting |
| `pelican`   | Keeps Keebdex's images on Cloudflare and in R2 | Porting |
| `magpie`    | Crawls forum interest checks and group buys    | Porting |

`bowerbird` and `pelican` take over the artisan sync and the image jobs from
[catalog-sync](https://github.com/keebdex/catalog-sync), which keeps only its
vendor keyset scraper until `magpie` replaces it.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run pre-commit install
```

```bash
cp .env.example .env
```

Fill in `.env`, then run jobs with `uv run --env-file .env <job>`. In GitHub
Actions the same names come from the repository secrets.

## lorikeet

Crawls the RAL Classic, Design, and Effect colors with their hex values from
[ralcolorchart.com](https://www.ralcolorchart.com) and adds the ones Keebdex
lacks to its `colors` table, for matching keyset colors.

```bash
uv run --env-file .env lorikeet ral --dry-run
```

- With no flag it writes. `--dry-run` reads Keebdex and reports what it would
  add. `--csv PATH` writes the crawl to a CSV file instead and needs no
  database.
- Colors are only ever added, matched by system and code. A color already in
  Keebdex is left as it is even when the chart shows another hex or name, since
  staff may have fixed it; those differences are listed in the report for a
  person to check. Nothing is deleted.
- Every page must load and show colors, and the crawl must find at least 90% as
  many colors as Keebdex already has; otherwise nothing is written.
- `.github/workflows/lorikeet.yml` runs it from the Actions tab; manual runs are
  dry runs unless the box is unticked, and the report lands in the run's
  summary. Its monthly schedule is paused (commented out) until a dry run
  against Keebdex has been checked.

The last crawl found 2,531 colors:

| Range       | Code format         | Colors | Names |
| ----------- | ------------------- | ------ | ----- |
| RAL Classic | `1000`              | 216    | Yes   |
| RAL Design  | `000 15 00` (H L C) | 1,825  | Yes   |
| RAL Effect  | `110-1`, `840-M`    | 490    | No    |

Single colors don't need a crawl: the Add Color form in Keebdex looks a RAL
Design code up on ralcolorchart.com as it is typed.

## bowerbird

Syncs artisan sculpts and colorways into Keebdex from makers' Google Docs and
maker websites. It is a port of catalog-sync's artisan sync, which still runs in
production: run bowerbird only as a dry run until catalog-sync's `artisan-sync`
workflow stops.

```bash
uv run --env-file .env bowerbird gdoc --dry-run
```

```bash
uv run --env-file .env bowerbird scrape --dry-run
```

- `gdoc` syncs every published maker with `document_ids`; `scrape` syncs the
  maker websites (`alpha-keycaps`, `keycat`). `--maker ID` limits a run to one
  maker (repeatable).
- Each owns its rows (`source` `gdoc` or `scraper`) and never touches the
  others. Synced fields are patched unless locked in Keebdex
  (`overridden_fields`); colorways gone from the source are deleted, or marked
  deleted while a user collection holds them; a sculpt gone from the source is
  deleted only when nothing points at it any more.
- A deleted doc makes the sync partial (rows from it are kept); a maker whose
  docs are all gone has its sync disabled. A maker that fails is logged and
  skipped, and the run still succeeds.
- Colorway images are uploaded to Cloudflare Images under fixed ids. Nothing
  here deletes an image: `pelican prune` removes the unused ones.
- `.github/workflows/bowerbird.yml` runs either or both from the Actions tab, as
  a dry run unless the box is unticked. While catalog-sync still runs the
  artisan sync (23:00 UTC), it also runs daily at 21:30 UTC, always as a dry
  run, so its log can be checked against what catalog-sync then writes.
- Slugs come from python-slugify; `docs/slugs.md` explains the map that keeps
  the slugs catalog-sync gave unusual names, and the check to run before
  bowerbird takes over.

## pelican

Keeps Keebdex's images on Cloudflare Images and backed up in Cloudflare R2: a
port of catalog-sync's image jobs, which still run in production. Run pelican
only as a dry run until catalog-sync's `image-sync`, `image-prune`, and `backup`
workflows stop.

```bash
uv run --env-file .env pelican sync --dry-run
```

- `sync` uploads the external images of published records to Cloudflare and
  points the records at them; `restore` re-uploads, from R2, referenced images
  Cloudflare no longer has; `prune` deletes images nothing references (a week
  old at least); `backup` copies new images to R2 and moves backups of deleted
  images to the trash once the bucket's lock rules allow.
- Each refuses numbers that look like a bad read (nothing listed, too many
  missing or orphaned at once) instead of acting on them.
- `.github/workflows/pelican.yml` runs one job from the Actions tab, as a dry
  run unless the box is unticked, plus a `database` job that dumps the database
  to R2. While catalog-sync still runs these jobs, it also runs each an hour
  before catalog-sync does, always as a dry run: sync and restore at 00:00 UTC,
  backup at 01:00, and prune at 23:00 before the 1st and the 15th. The
  `database` job runs by hand only until catalog-sync's backup is turned off.
- `docs/backups.md` is the operator guide for the R2 bucket: its locks, the
  trash, and restoring by hand.

## magpie

Crawls Geekhack's [Interest Checks](https://geekhack.org/index.php?board=132.0)
and [Group Buys](https://geekhack.org/index.php?board=70.0) boards into the
`ingest` schema of Keebdex's database, reads a keyset candidate out of each
keyset thread, and proposes the complete ones as Pending keysets for staff to
review in Keebdex's keyset Submissions. A port of huginn (a local crawler with
a web UI) without the UI: the choices huginn left to a person (profile, cover,
kits) are made from the topic, and what magpie can't settle waits in
`ingest.candidates` for staff.

```bash
uv run --env-file .env magpie crawl --dry-run
```

```bash
uv run --env-file .env magpie promote --dry-run
```

```bash
uv run magpie topic 127176
```

- `crawl` is one run (`ingest.crawl_runs`; one at a time). It lists both
  boards (`--board` for one) down to the newest last post the previous
  succeeded run saw, up to 5 pages each (`--max-pages`; run once with a large
  value to backfill). Every page is read, about one a second, before anything
  is written: a page that fails or shows no topics fails the run and writes
  nothing. Then it fetches the first post of due threads (new, with new
  replies, or a week since the last fetch while under 90 days old; 100 at most,
  `--fetch-limit`), keeps the post as it is, and adds a keyset candidate:
  profile, name, cover, kits, status, and a confidence per field. A topic that
  fails is retried with backoff; one that answers 404 is marked gone.
- `promote` inserts, newest first and at most 25 per run (`--limit`), the
  candidates magpie read completely (profile, name, cover, kits), posted in the
  last 90 days (`--max-age-days`), that Keebdex doesn't have: a Pending keyset
  with no submitter and its Pending kits (one per labelled kit image, or a Base
  kit with the cover, so the keyset shows up in the review queue). A candidate
  of a keyset Keebdex has (its URL, or the same profile and slug) gets that
  keyset as its `match_key` and waits for staff, as do incomplete and older
  ones; a different status is reported, never written.
- `topic` prints one topic as JSON and needs no database.
- Needs the `ingest` schema and the `forum` revision source (migration
  `20261119000000_ingest_schema.sql` in Keebdex's supabase repository), with
  `ingest` added to the Data API's exposed schemas.
- `.github/workflows/magpie.yml` runs the crawl, the promotion, or both from
  the Actions tab, as a dry run unless the box is unticked. Its schedule is off
  until the schema is applied and a dry run has been checked.

## License

[MIT](LICENSE)
