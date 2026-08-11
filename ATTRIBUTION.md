# Attribution and licensing

This project combines public data sources under different licences. This file is the single source of truth for what came from where and what may be redistributed.

**Status of this file: incomplete.** Licence terms marked `TO VERIFY` have not yet been read in full. Each source's terms are recorded at the time of first ingest in `pipeline/sources/{name}/INGEST.md`; this file is the roll-up. The `area_jukyu` row remains unresolved. The project owner accepted that risk for the 2026-08-11 frontend launch; this is not permission, legal clearance, or a change to the upstream terms. Future additions to the published dataset still require a resolved attribution/licence decision.

## Sources

| Source | Provider | What we use | Licence | Redistributable |
|---|---|---|---|---|
| [All-Japan-Grid](https://github.com/lutelute/All-Japan-Grid) | community project, built on OpenStreetMap | Transmission line corridors, substations, generation sites, with WGS84 coordinates | Data: **ODbL**; code: MIT | Yes, with attribution and share-alike on derived databases |
| OpenStreetMap | OSM contributors | Upstream of the above | **ODbL** | Yes, with attribution and share-alike |
| Global Energy Monitor — Global Integrated Power Tracker | Global Energy Monitor | Unit-level generation attributes **with latitude/longitude** (fuel, capacity, status, start year, operator) | **CC BY 4.0** | Yes, with attribution; recommended citation: *Global Integrated Power Tracker, Global Energy Monitor, March 2026 release* |
| エリア需給実績 | the ten general transmission and distribution utilities | Hourly area demand and generation by source type, 2016–present | Per-utility terms of use, not uniform. Kyushu checked: reproduction/quotation/republication without prior consent is prohibited | `TO VERIFY` — but note we publish **derived estimates**, not the source CSVs |
| 系統情報サービス | OCCTO | Trunk-line power flow by circuit, 30-minute resolution | OCCTO terms of use: free to use **with attribution**; derived/edited content must additionally state that it was modified | **Likely yes** — verify the terms text before publishing |
| ユニット別発電実績公開システム | OCCTO | **Per-unit** generation output, 30-minute resolution, 2024-03-25 onwards | Same OCCTO terms as above | **Likely yes** — same caveat |
| 国土数値情報 発電施設 (P03) | MLIT | Power facility points **with coordinates**, by generation type, 2013 vintage | 国土数値情報 利用約款 (government open data) | Yes, with attribution |
| 水力発電所データベース | 電力土木技術協会 (JEPOC) | Hydro plant modelling parameters (type, head, flow, catchment) — **no coordinates** | **Site policy prohibits reproduction/transmission/modification/republication without prior permission** | **NO** — see note below |

## Removed sources (2026-08-07)

Three sources were ingested but never consumed by any published artifact, and
have been removed together with their raw data:

| Source | Size | Why it was dropped |
|---|---|---|
| スポット市場取引結果 (JEPX) | 40 MB | Price data. No published layer used it. |
| 再生可能エネルギー 事業計画認定情報 (METI) | 64 MB | Municipality-level FIT/FIP totals. No published layer used it. |
| 連系線運用容量 (OCCTO) | 27 MB | Interconnector capacity. **The published flow data contains no interconnectors at all** (0 of 316 line names), so these values match nothing we draw. |

Keeping unused sources meant carrying unresolved licence rows indefinitely for
data that was never distributed. All three are publicly re-fetchable without
credentials, and the ingest code remains in git history.

## Verification notes (2026-08-07)

**Global Energy Monitor — resolved.** The licence is
**Creative Commons Attribution 4.0 International (CC BY 4.0)**, stated at
https://globalenergymonitor.org/creative-commons-license . Redistribution and
derivative works are permitted with attribution, so it is compatible with the
ODbL share-alike that applies to our published files.
⚠️ The licence is **not** linked from the project page, the download page or the
tracker page — it was found via the sitemap. The ingest record now records the
same CC BY 4.0 conclusion.

**JEPOC (電力土木技術協会).** `jepoc.or.jp/info/sitepolicy.html` states that,
outside of printing/saving for personal use, reproducing, transmitting,
modifying, excerpting, broadcasting or republishing the site's contents is
prohibited by copyright law, and that prior contact is required to obtain
permission. **`sites.geojson` currently publishes 518 hydro records whose
*list* — name, operator, generation method, capacity — originates from this
database.** That is closer to republication than to derived analysis.
**To resolve:** obtain permission, or rebuild the hydro list from
国土数値情報 P03 (水力 698 / 揚水 46, government open data, coordinates included)
and keep JEPOC for local modelling parameters only.

**エリア需給実績.** Kyushu's site policy (`kyuden.co.jp/td/sitepolicy/rule.html`)
prohibits reproduction/quotation/republication without prior consent. The other
nine were not checked and their terms are not uniform. ⚠️ Note the distinction:
we do **not** republish these CSVs. `sites.geojson` carries per-site *estimates*
computed by apportioning area totals — a derived result, not a copy. The
attribution wording should make that explicit.

**エリア需給実績 — follow-up check (2026-08-11).** Official pages were checked
for the remaining operators: [北海道](https://www.hepco.co.jp/network/using/legal.html),
[東京](https://www.tepco.co.jp/legal/),
[中部](https://powergrid.chuden.co.jp/denkiyoho/siteinfo.html),
[中国](https://www.energia.co.jp/nw/guide/index.html), and
[四国](https://www.yonden.co.jp/terms/) each state, in substance, that use beyond
private use or reproduction/adaptation/distribution requires permission or is
not allowed. The [東北 data page](https://setsuden.nw.tohoku-epco.co.jp/realtime_jukyu.html),
[関西 conditions page](https://www.kansai-td.co.jp/denkiyoho/siteinfo.html), and
[沖縄 data page](https://www.okiden.co.jp/business-support/service/supply-and-demand/)
did not provide a clear general redistribution grant on the pages checked.
This confirms that the ten-company source does not have a uniform open-data
licence. The project publishes derived estimates rather than source CSVs, but
that distinction has not been confirmed as sufficient permission; **L3 remains
unresolved**. The project owner explicitly chose not to block the 2026-08-11
frontend launch on this issue. This must not be described as permission or
licence clearance.

⚠️ Nothing above is legal advice. It records what the published terms say.

## Rules

1. **Record at ingest time, not later.** Every source records `source_url`, `licence`, and `redistributable` in its `INGEST.md` when it is first fetched. Reconstructing this months afterwards does not work.
2. **Published artifacts carry attribution.** `manifest.json` includes a per-layer attribution field; the web map renders it.
3. **Share-alike propagates.** Any published artifact derived from ODbL data is itself subject to ODbL. Layers are kept separable so this does not contaminate unrelated outputs.
   ⚠️ **A source that forbids redistribution cannot be mixed into an ODbL artifact.**
   ODbL requires granting downstream recipients the right to redistribute; "this
   file is ODbL, except for the rows from source X" is not a coherent offer. Such a
   source must live in its own file under its own terms, or not be published at all.
4. **`TO VERIFY` normally blocks publication, not development.** A source may be ingested and explored locally while its terms are still being read. Any exception requires an explicit owner decision recorded as a launch risk; that exception is not a licence clearance.
5. **Sources under non-commercial licences are excluded by design.** Demand resolution stops at the area level partly for this reason — see `CLAUDE.md`.

## Code

Code in this repository is MIT licensed. Data artifacts are not covered by that licence; see the table above.
