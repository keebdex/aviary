# aviary

The data jobs behind [Keebdex](https://keebdex.org), each a module named after a
bird:

| Module      | What it does                                       | Status  |
| ----------- | -------------------------------------------------- | ------- |
| `lorikeet`  | Adds reference color palettes (RAL) to Keebdex     | Ready   |
| `magpie`    | Crawls forum interest checks and group buys        | Planned |
| `bowerbird` | Syncs artisan catalogs from Google Docs and makers | Planned |

`magpie` and `bowerbird` take over from
[catalog-sync](https://github.com/keebdex/catalog-sync), which is ported here
step by step.

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
- `.github/workflows/lorikeet.yml` runs it from the Actions tab; manual runs
  are dry runs unless the box is unticked, and the report lands in the run's
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

## License

[MIT](LICENSE)
