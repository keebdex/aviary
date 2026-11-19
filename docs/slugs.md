# Slugs

`common/slug.py`'s `url_slugify` builds sculpt slugs (page keys) and the slugged
names maker scrapers hash into colorway ids. It is python-slugify, after
dropping the punctuation Keebdex's `urlSlugify` drops (`*+~.()'"!:@,/`), with
`&`, `$`, `%`, `<`, `>`, and `|` spelled out and underscores kept. Ordinary
names get the same slug as in Keebdex and as catalog-sync gave them.

## Names catalog-sync slugged differently

catalog-sync used npm `slugify`, which drops every character missing from its
map (CJK, `×`, `²`) and turns curly quotes into straight ones it keeps, where
python-slugify transliterates or removes them. Rather than migrating the rows
whose slug would change, `LEGACY_SLUGS` maps those names (trimmed) to the slug
catalog-sync gave them, so they keep their keys; every other name, including new
ones and renames, follows python-slugify.

The 29 entries came on 2026-11-19 from catalog-sync's local `db/` dumps of the
Google Docs (2,761 sculpts, written 2026-09-30) and a live scrape of the maker
websites: 15 sculpts (`404artisans`, `asianjoyco`) and the alpha-keycaps
colorways with curly quotes.

## Before bowerbird takes over

Names synced after the dumps may be missing. Recheck against the database: list
the `name` of every `artisan_sculpts` row with `source` `gdoc` or `scraper` (and
of `scraper` colorways), slug each with catalog-sync's `urlSlugify` and with
`url_slugify`, and add every name whose two slugs differ to `LEGACY_SLUGS`. A
missed name only means that sculpt is recreated under its new slug on the first
sync, with its colorways.

## Keebdex itself

Keebdex slugs app-made records with npm `slugify` (`app/utils/slug.ts`). For
names with such characters an app-made sculpt and a synced one get different
slugs; they are separate rows anyway (`source` differs), so nothing collides.
