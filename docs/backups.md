# Backups

The operator guide for `pelican backup`, moved from catalog-sync.

The R2 bucket holds the original of every Cloudflare image under `images/<id>`
(including uploads from the Keebdex app, which exist nowhere else) and daily
database dumps under `db/daily/` (the dump of the 1st is also kept under
`db/monthly/`). Retention is set on the bucket by infrastructure:

| Prefix      | Lock         | Lifecycle            |
| ----------- | ------------ | -------------------- |
| `images/`   | indefinitely | none                 |
| `db/daily/` | none         | expire after 30 days |

## Trash (driven by the bucket rules)

`pelican backup` moves the backup of an image that is gone from Cloudflare to
`trash/images/<id>`, but only once the bucket's lock rules allow it to leave
`images/`. It reads those rules on every run, so the infrastructure that owns
the bucket decides everything:

- `images/` locked indefinitely (today): nothing moves.
- `images/` locked for N days: unused backups older than N days move to the
  trash.
- `trash/` follows its own rules: an indefinite lock keeps everything; a 90-day
  lock plus a 90-day expiry lifecycle rule deletes after 90 days.

Reading the rules needs `R2_API_TOKEN`, a read-only Cloudflare API token.
Without it, or if the rules can't be read, the trash is skipped.

## Enabling the trash

The trash is shipped but idle: `images/` is locked indefinitely and
`R2_API_TOKEN` isn't set. Nothing in aviary needs to change to turn it on;
follow these steps in order.

1. **Read token (infrastructure).** Create a read-only token, either an R2 token
   with _Admin Read only_ or an API token with _Workers R2 Storage: Read_, and
   store it as the `R2_API_TOKEN` repository secret. Keep it separate from the
   `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` token: an R2 token's S3 secret is
   derived from the token itself, so giving the backup token admin rights would
   let it remove the bucket locks.
2. **Check the token.** Run `uv run --env-file .env pelican backup --dry-run`
   with `R2_API_TOKEN` in `.env`. The log should show
   `Unused backups N, still locked N, to trash 0` and no
   `Unable to read the bucket lock rules` warning; with `images/` still locked
   indefinitely, nothing is to trash.
3. **Rules for `trash/` (infrastructure), before unlocking `images/`.** Decide
   how long trashed backups live: an indefinite lock keeps everything, while a
   90-day lock plus a 90-day expiry lifecycle rule deletes them after 90 days.
   Set this first, so the first moved backup is already protected.
4. **Finite lock on `images/` (infrastructure).** Replace the indefinite lock
   with an `Age` lock of N days (e.g. 30). This is the switch: from the next
   run, unused backups older than N days move to `trash/images/`.
5. **Dry run again.** `uv run --env-file .env pelican backup --dry-run` now
   lists the backups it would trash. If that's more than 10% of all backups the
   run refuses; check that the Cloudflare listing is complete before rerunning
   with `--force-trash`.
6. **Watch the first runs** of the pelican workflow's backup job:
   `Trashed N backups`, no `Unable to trash` errors, and the object count under
   `trash/images/` growing in the R2 dashboard.

To stop the trash again, put the indefinite lock back on `images/`. Backups
already in `trash/images/` stay under the `trash/` rules.

## Restoring

`pelican restore` re-uploads referenced images missing from Cloudflare on its
own. To restore by hand, re-upload images to Cloudflare under their old ids (the
key without `images/` or `trash/images/`) so stored URLs work again, and restore
a dump into a scratch database with `pg_restore` before copying back what's
needed.
