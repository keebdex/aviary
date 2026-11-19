# aviary

The data jobs behind [Keebdex](https://keebdex.org), each a module named after a
bird:

| Module      | What it does                                   | Status  |
| ----------- | ---------------------------------------------- | ------- |
| `lorikeet`  | Adds reference color palettes (RAL) to Keebdex | Ready   |
| `bowerbird` | Syncs artisan catalogs from makers and Docs    | Porting |
| `pelican`   | Keeps Keebdex's images on Cloudflare and in R2 | Porting |
| `magpie`    | Crawls forum interest checks and group buys    | Planned |

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
  a dry run unless the box is unticked. It has no schedule yet.
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
  to R2. It has no schedule yet.
- `docs/backups.md` is the operator guide for the R2 bucket: its locks, the
  trash, and restoring by hand.

## License

[MIT](LICENSE)
