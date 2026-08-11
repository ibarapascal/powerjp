# Operations

This is the public-safe runbook for the live powerjp frontend and its data
publication path. Machine-specific paths, hostnames, credentials, and LaunchAgent
installation details belong in private operations notes, not in this file.

## Production topology

```text
private Dagster dev
  ├─ ingest → raw/ → structured/ → publish/
  ├─ publish upload (hash objects, then manifest)
  └─ freshness check
              ↓
Cloudflare R2 → data.powerjp.mkhr.co.jp/powerjp/v1
              ↓
Cloudflare Pages → powerjp.mkhr.co.jp
```

- Frontend: <https://powerjp.mkhr.co.jp/>
- Data base: <https://data.powerjp.mkhr.co.jp/powerjp/v1>
- Pages project: `powerjp`
- Dagster is intentionally a private development process, not a public service
  and not a containerized production API.

## Refresh contract

The Dagster `refresh_30min` schedule updates the dynamic publish layer every 30
minutes. The freshness checker runs independently on the same interval and checks
the published manifest. The browser checks the manifest every 5 minutes and
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

The custom DNS record is a CNAME from `powerjp.mkhr.co.jp` to the Pages project
domain and should use Cloudflare **Proxied / orange cloud** status. The data
custom domain is separate and must keep the R2 CORS allow-list aligned with the
production and staging page origins.

## Verification checklist

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://powerjp.mkhr.co.jp/
curl -fsS https://data.powerjp.mkhr.co.jp/powerjp/v1/manifest.json | jq 'keys'
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

The private machine runs two owned agents:

- `com.powerjp.dagster`: persistent Dagster dev server, bound to loopback only.
- `com.powerjp.freshness`: independent 30-minute manifest freshness check.

Use the private daemon ledger to inspect them and their logs. A healthy PID is not
enough: confirm recent log output, a recent manifest timestamp, and no duplicate
powerjp worker. If a run fails, inspect the Dagster/freshness logs, fix the source
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

Dagster remains a bare dev process by design. Cloudflare Pages and R2 handle only
the public frontend and published artifacts; they do not replace the private
compute pipeline.

## Secret boundary

- Actual R2 credentials, webhook URLs, and Mapbox token values live only in local
  ignored environment files or provider-side environment settings.
- `.env.example` files contain names and empty/example values only.
- Raw data, local Dagster state, build output, Wrangler state, logs, and private
  operations notes are not repository inputs.
- The Mapbox browser token is a public client token by design; its protection is
  the Mapbox URL allow-list, not secrecy.
- Before any public-repository operation, inspect both tracked history and the
  complete candidate tree for credentials. If a credential was ever committed,
  rotate it before publishing; deleting the file is not enough.

## Related records

- [2026-08-11 production release](releases/2026-08-11-production.md)
- [data-source attribution](../ATTRIBUTION.md)
