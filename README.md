# powerjp

Open data pipeline and visualization for Japan's power grid — generation, network topology, power flow, and area supply-demand.

> **Status: early development.** Nothing works yet. This repository is currently private and the structure is still moving.

## What this is

Japan publishes a surprising amount about its power system: transmission-line topology and available capacity are disclosed by law, area-level supply and demand are published hourly by all ten TSOs going back to 2016, and OCCTO publishes trunk-line power flow at 30-minute resolution. What does not exist is a single place where these are joined, aligned, and shown on a map over time.

`powerjp` builds that: a reproducible pipeline that ingests the public sources, aligns them, and publishes static artifacts to a CDN, plus a web map that reads them.

## Scope

**In scope**

- Transmission network geometry (lines, substations) and generation sites
- Trunk-line power flow (30-minute) and interconnector operating capacity
- Area supply and demand by source type (hourly, 10 areas, 2016–present)
- Spot electricity prices by area
- A derived layer on top of the above: net position, line utilisation, price spread, and supply-demand gaps under network constraints

**Out of scope**

- Building-level demand estimation — demand resolution stops at the 10 TSO areas
- Distribution-level (6.6 kV feeder) geometry — not publicly available
- Operational-grade simulation — line impedance parameters have no authoritative public source, so this project displays *observed* flow rather than computing it

## Architecture

```
Ingest   →  storage/raw/{source}/{date}/          manual / assisted / auto
─────────────────────── boundary ───────────────────────
Process  →  structured → derived → published      fully deterministic, replayable
                                       ↓
                          Cloudflare R2  →  web map
```

Everything downstream of `storage/raw/` is deterministic and can be replayed from scratch. Credentials, logins, and manual downloads all stay upstream of that line.

Orchestrated with [Dagster](https://dagster.io/). Geometry in PostGIS, time series in Parquet, vector tiles built with tippecanoe, served from Cloudflare R2. Frontend is React + Vite + Mapbox GL JS.

## Data sources and licensing

This project combines sources under several different licences, some of which restrict redistribution. See [`ATTRIBUTION.md`](ATTRIBUTION.md) for the per-source breakdown. Published artifacts carry attribution metadata in `manifest.json`.

## Licence

Code is MIT. Data artifacts derived from third-party sources are governed by those sources' licences — see `ATTRIBUTION.md`.
