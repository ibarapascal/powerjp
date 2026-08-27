# Operations

This is the public-safe runbook for the live powerjp frontend and its data
publication path. Machine-specific paths, hostnames, credentials, and LaunchAgent
installation details belong in private operations notes, not in this file.

## Production topology

```text
mkhr-data platform Dagster (power location, private compute)
  ├─ ingest → shared raw tree (this repo reads it via storage/raw symlinks)
  ├─ powerjp_publish → structured/ → publish/ → upload (hash objects, then manifest)
  └─ freshness check (this repo's independent agent)
              ↓
Cloudflare R2 → data.powerjp.mkhr.co.jp/powerjp/v1
              ↓
Cloudflare Pages → powerjp.mkhr.co.jp
```

Since 2026-08-27 all scheduling (ingest and the 30-minute publish) lives in the
mkhr-data repository's Dagster (`pipelines/dagster/power`). This repository
contains only pure CLI pipeline code that the platform invokes.

- Frontend: <https://powerjp.mkhr.co.jp/>
- Data base: <https://data.powerjp.mkhr.co.jp/powerjp/v1>
- Pages project: `powerjp`
- Orchestration runs inside the mkhr-data platform Dagster on the same private
  machine; it is not a public service and not a containerized production API.

## Source of truth and change routing

There are four different “sources of truth”; keeping them separate prevents a
frontend change from being mistaken for a data release:

- **Code and technical decisions**: the Git working tree and its pushed branch.
- **Raw history**: the private compute machine's `storage/raw/`; it is the
  irreplaceable input and is never uploaded to R2.
- **Local publish output**: rebuildable from raw/structured data. It is the
  input to the uploader, not a second hand-edited data source.
- **Public snapshot**: R2's `manifest.json` and the hash-named objects it points
  to. Never edit an object or manifest by hand.

There is no long-running public application API. The public data API is the
static contract `manifest.json` → hash-named JSON/GeoJSON objects under the
configured prefix. A change to that contract must update the producer and the
frontend together.

| Change | Local check | Production action |
|---|---|---|
| `web/**`, `index.html`, `web/public/**` | `cd web && pnpm build` | Deploy the new Pages build; no sync to private compute |
| `pipeline/sources/**` | CLI `--dry-run` or a small date/window | Sync code to the private compute checkout; run the smallest ingest → publish → R2 scope, or let the next schedule do it |
| `pipeline/structured/**` or `geojson.py` | Run the affected deterministic step and inspect counts | Sync to the private compute checkout, rebuild the affected publish layer, then upload `dynamic` or `all` as appropriate |
| `pipeline/publish/spec.py` or `upload.py` | `--dry-run` and manifest/object review | Treat as a publication-contract change; coordinate producer and frontend, then publish `all` when the object set changes |
| schedule config | change in the mkhr-data repo (`pipelines/dagster/power`) | Deploy via that repo's procedure; verify the schedules are `RUNNING` |
| R2/Pages env, CORS, DNS, Mapbox restrictions | Provider-side read-only check | Change in Cloudflare/Mapbox settings; never commit the value |

The private compute checkout is a runtime mirror of the code source, not a
second Git history. Code is synchronized explicitly while `storage/`, `.env`,
`.venv/`, `web/node_modules/`, `web/dist/`, and logs remain
machine-local. The exact paths, resource gate, and LaunchAgent commands are in
the ignored private operations notes referenced by `CLAUDE.local.md`.

## Cold-start sequence

When resuming this project after context loss, read in this order:

1. `CLAUDE.md` for architecture and hard constraints.
2. `TODO.md` for the current backlog and release state.
3. This file for the public deployment/data-release procedure.
4. `CLAUDE.local.md` and the private operations record for machine paths,
   credentials locations, ports, and the private compute safety gate.
5. `DEPLOY.local.md` for the current private release checklist and the exact
   code-sync/LaunchAgent procedure.
6. The relevant source `INGEST.md`, `web/README.md`, or
   `web/REQUIREMENTS.md` before changing that area.

The minimum safe handoff is: identify the changed layer, validate locally, make
the working tree clean and push the release commit, sync only code when the
private compute checkout is involved (schedule changes go through the mkhr-data
repo instead), publish through `pipeline.publish.upload`, and run the
production smoke checks below. Do not “fix” production by editing R2 directly.

## Refresh contract

The mkhr-data `powerjp_publish_30min` schedule (10,40 past each hour JST)
rebuilds and uploads the dynamic publish layer, five minutes after the platform's
ingest tick. The platform's daily backfill (01:20 JST) fills gaps in the last
7 days of OCCTO data and force-refreshes the current and previous month of
`area_jukyu` (the monthly CSVs accumulate in place; Chubu only hosts two
months, so skipping a month loses it permanently). The freshness checker runs
independently on the same interval and checks the published manifest. The browser checks the manifest every 5 minutes and
reloads only changed data objects; it does not force a full-page reload, so map
state and the time slider remain in place.

The manifest is written last. Data objects use content-hash names, static objects
are immutable, dynamic objects use a short cache lifetime, and the manifest is
uncached. This is the release atomicity boundary.

## Routine data operations

Run these from the repository root, with credentials supplied through a local
ignored `.env` file:

```bash
# Check the planned upload without changing R2.
uv run python -m pipeline.publish.upload --scope all --dry-run

# First publication or after static publish changes.
uv run python -m pipeline.publish.upload --scope all

# Normal 30-minute update.
uv run python -m pipeline.publish.upload --scope dynamic

# Rebuild local publish artifacts before uploading, when structured data changed.
uv run python -m pipeline.publish.geojson
```

The upload script is the only place that defines the R2 object set. Do not upload
raw history, do not overwrite a hash object, and do not manually edit
`manifest.json`.

## Frontend deployment

Build with the production data base and the public Mapbox token supplied through
the shell or Pages environment. Never put either value into a committed file.

```bash
cd web
pnpm install --frozen-lockfile
VITE_DATA_BASE=https://data.powerjp.mkhr.co.jp/powerjp/v1 pnpm build
cd ..
npx wrangler pages deploy web/dist \
  --project-name powerjp \
  --branch main
```

This is a frontend-only release. It does not refresh R2 data and does not
require restarting Dagster. If both frontend and data contract changed, publish
the compatible data snapshot and deploy the frontend as one coordinated release;
the R2 manifest remains the atomic switch for data objects.

The custom DNS record is a CNAME from `powerjp.mkhr.co.jp` to the Pages project
domain and should use Cloudflare **Proxied / orange cloud** status. The data
custom domain is separate and must keep the R2 CORS allow-list aligned with the
production and staging page origins.

## Verification checklist

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://powerjp.mkhr.co.jp/
curl -fsS https://data.powerjp.mkhr.co.jp/powerjp/v1/manifest.json | jq 'keys'
curl -fsS https://data.powerjp.mkhr.co.jp/powerjp/v1/manifest.json \
  | jq '[.static[], .dynamic[]] | map(.file)'
curl -fsSI -H 'Origin: https://powerjp.mkhr.co.jp' \
  https://data.powerjp.mkhr.co.jp/powerjp/v1/manifest.json
```

Also verify these frontend entry points return 200:
`/favicon.svg`, `/robots.txt`, `/sitemap.xml`, `/llms.txt`, and
`/.well-known/ai.json`.

Expected headers:

- manifest: `no-cache, no-store, must-revalidate`
- dynamic data: short `max-age`
- hash-named static data: long-lived `immutable`
- R2 CORS: production page origin allowed for `GET`/`HEAD` only

If the Pages hostname works but the custom hostname briefly returns 522 after a
DNS change, check the Pages custom-domain status and certificate first. Do not
redeploy the data or restart the compute machine just for DNS propagation.

## Monitoring and recovery

The private machine runs one owned agent plus the platform Dagster:

- `com.powerjp.freshness`: independent 30-minute manifest freshness check.
- Ingest and publish runs live in the mkhr-data Dagster (`com.mkhr.*` agents,
  operated from that repo); inspect run history there.

Use the private daemon ledger to inspect them and their logs. A healthy PID is not
enough: confirm recent log output, a recent manifest timestamp, and no duplicate
powerjp worker. If a run fails, inspect the platform Dagster/freshness logs, fix the source
or data issue, then run the smallest affected scope. Use `--scope dynamic` for a
normal retry; use `--scope all` only when static artifacts changed or the manifest
is known to be incomplete.

The previous manifest remains usable while a new upload is in progress. If a bad
manifest is published, restore the last known-good manifest from the local publish
backup or R2 object history, then verify the page and CORS again. Do not delete the
current good hash objects during recovery.

## Resource and safety rules

The compute machine also runs unrelated containers and scripts. For powerjp:

- never shut down or reboot the machine;
- never run global `pkill`, `docker compose down`, or broad cleanup commands;
- operate only the `com.powerjp.*` agents;
- keep uploads serial and prefer the smallest scope;
- check memory, disk, active processes, and ports before expensive backfills;
- stop and report if a task risks OOM or interferes with another service.

Cloudflare Pages and R2 handle only
the public frontend and published artifacts; they do not replace the private
compute pipeline.

## Secret boundary

- Actual R2 credentials, webhook URLs, and Mapbox token values live only in local
  ignored environment files or provider-side environment settings.
- `.env.example` files contain names and empty/example values only.
- Raw data, build output, Wrangler state, logs, and private
  operations notes are not repository inputs.
- The Mapbox browser token is a public client token by design; its protection is
  the Mapbox URL allow-list, not secrecy.
- Before any public-repository operation, inspect both tracked history and the
  complete candidate tree for credentials. If a credential was ever committed,
  rotate it before publishing; deleting the file is not enough.

## Related records

- [2026-08-11 production release](releases/2026-08-11-production.md)
- [data-source attribution](../ATTRIBUTION.md)
