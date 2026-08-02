# Attribution and licensing

This project combines public data sources under different licences. This file is the single source of truth for what came from where and what may be redistributed.

**Status of this file: incomplete.** Licence terms marked `TO VERIFY` have not yet been read in full. Each source's terms are recorded at the time of first ingest in `pipeline/sources/{name}/INGEST.md`; this file is the roll-up. **No artifact is published to the CDN before its source row here is resolved.**

## Sources

| Source | Provider | What we use | Licence | Redistributable |
|---|---|---|---|---|
| [All-Japan-Grid](https://github.com/lutelute/All-Japan-Grid) | community project, built on OpenStreetMap | Transmission line corridors, substations, generation sites, with WGS84 coordinates | Data: **ODbL**; code: MIT | Yes, with attribution and share-alike on derived databases |
| OpenStreetMap | OSM contributors | Upstream of the above | **ODbL** | Yes, with attribution and share-alike |
| Global Energy Monitor — Global Integrated Power Tracker | Global Energy Monitor | Unit-level generation attributes **with latitude/longitude** (fuel, capacity, status, start year, operator) | **Creative Commons Public License** — confirmed via the download form's licence checkbox | **Likely yes** with attribution; recommended citation: *Global Integrated Power Tracker, Global Energy Monitor, March 2026 release* |
| エリア需給実績 | the ten general transmission and distribution utilities | Hourly area demand and generation by source type, 2016–present | Per-utility terms of use, not uniform | `TO VERIFY` — differs by utility |
| 系統情報サービス | OCCTO | Trunk-line power flow by circuit, 30-minute resolution | OCCTO terms of use: free to use **with attribution**; derived/edited content must additionally state that it was modified | **Likely yes** — verify the terms text before publishing |
| 連系線運用容量 | OCCTO | Annual interconnector operating capacity | Same OCCTO terms as above | **Likely yes** — same caveat |
| ユニット別発電実績公開システム | OCCTO | **Per-unit** generation output, 30-minute resolution, 2024-03-25 onwards | Same OCCTO terms as above | **Likely yes** — same caveat |
| 国土数値情報 発電施設 (P03) | MLIT | Power facility points **with coordinates**, by generation type, 2013 vintage | 国土数値情報 利用約款 (government open data) | Yes, with attribution |
| 水力発電所データベース | 電力土木技術協会 (JEPOC) | Hydro plant modelling parameters (type, head, flow, catchment) — **no coordinates** | `TO VERIFY` | `TO VERIFY` |
| 再生可能エネルギー 事業計画認定情報 | METI | FIT/FIP certified & installed capacity by municipality, quarterly | `TO VERIFY` | `TO VERIFY` |
| スポット市場取引結果 | JEPX | Area spot prices, 30-minute resolution | JEPX terms | `TO VERIFY` |

## Rules

1. **Record at ingest time, not later.** Every source records `source_url`, `licence`, and `redistributable` in its `INGEST.md` when it is first fetched. Reconstructing this months afterwards does not work.
2. **Published artifacts carry attribution.** `manifest.json` includes a per-layer attribution field; the web map renders it.
3. **Share-alike propagates.** Any published artifact derived from ODbL data is itself subject to ODbL. Layers are kept separable so this does not contaminate unrelated outputs.
4. **`TO VERIFY` blocks publication, not development.** A source may be ingested and explored locally while its terms are still being read; it may not be published to the CDN.
5. **Sources under non-commercial licences are excluded by design.** Demand resolution stops at the area level partly for this reason — see `CLAUDE.md`.

## Code

Code in this repository is MIT licensed. Data artifacts are not covered by that licence; see the table above.
