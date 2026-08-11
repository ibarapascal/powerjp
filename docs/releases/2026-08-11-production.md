# Production release — 2026-08-11

## Result

The powerjp frontend was deployed to Cloudflare Pages and bound to the production
domain:

- Site: <https://powerjp.mkhr.co.jp/>
- Data: <https://data.powerjp.mkhr.co.jp/powerjp/v1>
- Pages project: `powerjp`

The custom domain initially returned 522 while the Pages binding and certificate
were propagating. It returned 200 after the DNS/Pages binding completed.

## Release contents

- SEO/GEO metadata, favicon, robots, sitemap, JSON-LD, and AI-readable entry points
- R2 manifest-based data loading with content-hash objects
- Separate static and dynamic cache policies
- R2 CORS for the production page origin
- 5-minute browser manifest polling without forced full-page refresh
- 30-minute private Dagster refresh and independent freshness check

## Verification performed

The production homepage, favicon, robots, sitemap, `llms.txt`, and
`/.well-known/ai.json` returned HTTP 200. The R2 manifest returned HTTP 200 with
the production `Access-Control-Allow-Origin` header. The Pages deployment URL also
returned HTTP 200.

## Operational decisions

- Dagster remains a bare dev process on the private compute machine; it is not
  exposed as a public service and was not containerized.
- R2 stores only the current frontend snapshot plus the configured grace window;
  raw history stays on the private machine.
- The repository remains private. Public release will use a new repository with no
  inherited history after the final audit.
- The `area_jukyu` redistribution question remains unresolved. The owner chose not
  to block this frontend launch on that issue; this record is not a licence grant
  or legal clearance. See `ATTRIBUTION.md`.

## Information deliberately absent

This record contains no R2 access keys, secret values, webhook URLs, Mapbox token
value, Cloudflare account/zone identifiers, private machine paths, internal IPs,
or raw/publish data. Those remain outside git and are managed through local ignored
environment files or provider settings.
