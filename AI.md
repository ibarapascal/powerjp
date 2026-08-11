# powerjp — GEO content source

> This file is the human-maintained source for the public AI/GEO summaries under `web/public/`.

## Project

`powerjp` is a reproducible open-data pipeline and static web map for Japan's electric power system. It joins public data about network topology, substations, generation facilities, and observed transmission-line power flow, then publishes inspectable GeoJSON and JSON artifacts through a CDN.

The production URL is `https://powerjp.mkhr.co.jp/`. Preview deployments use a separate Pages URL, but public metadata points to this final canonical domain.

## Public capabilities

- Explore Japan's transmission-line and substation geometry on a map.
- Inspect generation sites and unit-output layers.
- View observed trunk-line power flow at 30-minute intervals where the source provides it.
- Compare time-dependent layers through a shared time axis.
- Read source attribution and licence notes in the application.

## Geographic and temporal scope

- Geographic scope: Japan.
- Observed flow: 30-minute resolution.
- Unit output: 30-minute resolution where OCCTO publishes it.
- Area supply-demand: hourly upstream data used for derived estimates; source CSVs are not republished as a mirror.

Because the project describes a national network, it uses `geo.region=JP` and `geo.placename=Japan` rather than assigning a misleading single city coordinate.

## Sources

- All-Japan-Grid and OpenStreetMap for topology-derived geometry.
- OCCTO public system-information and unit-output data for observed values.
- MLIT National Land Numerical Information for public facility coordinates.
- Global Energy Monitor for generation attributes and coordinates.

## Boundaries and honest wording

- powerjp is a research and data-visualization project, not an operational control system.
- It visualizes observed flow; it does not claim that every displayed value is a newly calculated AC solution.
- Some flow records do not have a recoverable source-backed physical geometry.
- Data artefacts have source-specific redistribution conditions. The MIT code licence does not automatically license all data.

## Public machine-readable files

- `web/public/.well-known/ai.json`: structured English summary.
- `web/public/ai.html`: crawler- and human-readable English summary.
- `web/public/llms.txt`: short summary.
- `web/public/llms-full.txt`: detailed summary.

When product scope, data coverage, source terms, or the production URL changes, update this file and its derived public files together.
