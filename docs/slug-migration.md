# Slug migration before switching to a slug library

aviary keeps a line-by-line port of npm `slugify` (`common/slug.py`) because no
Python library gives the same slugs. To drop the port for `python-slugify`
later, the slugs it would compute differently must be migrated first, or the
next sync would delete these rows and create them again under new keys (losing
their history, suggestions, and collection items).

The lists below compare catalog-sync's `urlSlugify` with `python-slugify`
configured as close to it as it gets:

```python
slugify(
    re.sub(r"""[*+~.()'"!:@,/]""", "", text),
    replacements=[
        ["&", "and"],
        ["$", "dollar"],
        ["%", "percent"],
        ["<", "less"],
        [">", "greater"],
        ["|", "or"],
        ["_", "UNDERSCORE"],
    ],
).replace("underscore", "_")
```

They were computed on 2026-11-19 from catalog-sync's local `db/` dumps of the
Google Docs (written 2026-09-30, 2,761 sculpts) and a live scrape of the maker
websites. Recompute them against the database before migrating: names change.

## Sculpts (`artisan_sculpts.sculpt_id`)

- `404artisans` (gdoc): #094 × Christmas  
  `094-christmas` → `094-x-christmas`
- `asianjoyco` (gdoc): Almond Cookie |杏仁餅  
  `almond-cookie-or` → `almond-cookie-orxing-ren-bing`
- `asianjoyco` (gdoc): Black Sesame Roll |芝麻卷  
  `black-sesame-roll-or` → `black-sesame-roll-orzhi-ma-juan`
- `asianjoyco` (gdoc): BoloBao |菠蘿包  
  `bolobao-or` → `bolobao-orbo-luo-bao`
- `asianjoyco` (gdoc): Char Siu Bao | BBQ Pork Bun | 叉燒包  
  `char-siu-bao-or-bbq-pork-bun-or` →
  `char-siu-bao-or-bbq-pork-bun-or-cha-shao-bao`
- `asianjoyco` (gdoc): Dan Tat | Egg Tart | 蛋塔  
  `dan-tat-or-egg-tart-or` → `dan-tat-or-egg-tart-or-dan-ta`
- `asianjoyco` (gdoc): Dim Sum Teapot | 茶壶  
  `dim-sum-teapot-or` → `dim-sum-teapot-or-cha-hu`
- `asianjoyco` (gdoc): Feng Zhao | Chicken Feet |凤爪  
  `feng-zhao-or-chicken-feet-or` → `feng-zhao-or-chicken-feet-orfeng-zhua`
- `asianjoyco` (gdoc): Har Gow | Shrimp Dumpling | 虾饺  
  `har-gow-or-shrimp-dumpling-or` → `har-gow-or-shrimp-dumpling-or-xia-jiao`
- `asianjoyco` (gdoc): Jiaozi | Chinese Dumpling | 饺子  
  `jiaozi-or-chinese-dumpling-or` → `jiaozi-or-chinese-dumpling-or-jiao-zi`
- `asianjoyco` (gdoc): Mantou | Steamed Bun | 馒头  
  `mantou-or-steamed-bun-or` → `mantou-or-steamed-bun-or-man-tou`
- `asianjoyco` (gdoc): Mooncake | 月饼  
  `mooncake-or` → `mooncake-or-yue-bing`
- `asianjoyco` (gdoc): Shumai | 燒賣  
  `shumai-or` → `shumai-or-shao-mai`
- `asianjoyco` (gdoc): XLB | Soup Dumpling | 小笼包  
  `xlb-or-soup-dumpling-or` → `xlb-or-soup-dumpling-or-xiao-long-bao`
- `asianjoyco` (gdoc): Zongzi |粽子  
  `zongzi-or` → `zongzi-orzong-zi`

Renaming a sculpt's slug in Keebdex moves its page key and children with it
(`update_maker_sculpt_id`, on update cascade). For Google Docs sculpts, also set
`source_slug` so the sync keeps matching until the doc's slug and the library
agree.

## Maker website colorways (`artisan_colorways.colorway_id`)

The scrapers hash the slugged name into the colorway id
(`crc32("<maker>-<sculpt>-<slug>")`), so these ids would change. Google Docs
colorway ids are the doc's image object ids and do not depend on slugs.

- `alpha-keycaps/darth-looga`: D’ohnut  
  `dda00ca3` → `8df2b6b2` (`d'ohnut` → `dohnut`)
- `alpha-keycaps/darth-looga`: Munkii’s Dream  
  `447d0a05` → `c371aca5` (`munkii's-dream` → `munkiis-dream`)
- `alpha-keycaps/keypora`: Dementor’s Kiss  
  `12047f1e` → `a1162c13` (`dementor's-kiss` → `dementors-kiss`)
- `alpha-keycaps/keypora`: Munkii’s Dream  
  `b8641fb2` → `63ec67fe` (`munkii's-dream` → `munkiis-dream`)
- `alpha-keycaps/keypora`: Raison D’Etre  
  `60e8e288` → `ae76566c` (`raison-d'etre` → `raison-detre`)
- `alpha-keycaps/keypora`: “Arizona Meetup”  
  `2fffb1e7` → `aedf007c` (`"arizona-meetup"` → `arizona-meetup`)
- `alpha-keycaps/keypora`: D’va  
  `7ffe7325` → `bb6707d9` (`d'va` → `dva`)
- `alpha-keycaps/alpha-ape`: DK’s Banana  
  `85ade697` → `988cc49f` (`dk's-banana` → `dks-banana`)
- `alpha-keycaps/salvador`: Na’vi  
  `118fd63` → `e24e74ee` (`na'vi` → `navi`)
- `alpha-keycaps/prayge`: Munkii’s Prayer  
  `e0b8ee16` → `3f068de2` (`munkii's-prayer` → `munkiis-prayer`)
- `alpha-keycaps/prayge`: “She’ll say yes, Amen”  
  `77205275` → `c29ac19b` (`"she'll-say-yes-amen"` → `shell-say-yes-amen`)
- `alpha-keycaps/prayge`: God’s Nectar  
  `2840f233` → `9289094c` (`god's-nectar` → `gods-nectar`)
- `alpha-keycaps/prayge`: Volker’s Blessing  
  `70fdbdd0` → `dd4d03a1` (`volker's-blessing` → `volkers-blessing`)
- `alpha-keycaps/devoura`: Munkii’s Dream  
  `749a4380` → `91c7c04a` (`munkii's-dream` → `munkiis-dream`)
- `alpha-keycaps/devoura`: Hell’s Pigeon  
  `9a4c5f80` → `ed53bc84` (`hell's-pigeon` → `hells-pigeon`)
- `alpha-keycaps/lickely`: OG D’Or  
  `508d1a4` → `5c4a6e47` (`og-d'or` → `og-dor`)

## Also

- Keebdex itself slugs with npm `slugify` (`app/utils/slug.ts`): switching
  aviary alone would make app-made and synced slugs differ for names like these.
