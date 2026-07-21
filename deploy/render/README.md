# Render deployment (Blueprint)

Deploy the Dograh dashboard + API to [Render](https://render.com) using the
[`render.yaml`](render.yaml) Blueprint in this folder.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/dograh-hq/dograh)

> After clicking, set **Blueprint Path** to `deploy/render/render.yaml` on
> Render's setup page — the Blueprint lives in this folder, not the repo root,
> so Render won't auto-detect it.

## What the Blueprint provisions

| Service | Type | Notes |
|---|---|---|
| `dograh-api` | Web (image) | The monolith API image — runs migrations, the API, ARQ workers, and the telephony/campaign singletons in one container. Keep at **one instance**. |
| `dograh-ui` | Web (image) | Next.js dashboard. Reaches the API over Render's private network at `http://dograh-api:8000`. |
| `dograh-postgres` | Managed Postgres | `pgvector` enabled by the app migrations. Free tier is 1 GB and **expires after 30 days**. |
| `dograh-redis` | Managed Key Value | Redis-compatible, internal-only. |

## Not equivalent to a self-hosted deploy — no calls on Render

Render has **no UDP ingress**, and *every* Dograh call — telephony **and** the
in-dashboard "test" call — uses WebRTC media, which is UDP. So this deploy lets you
**build and manage agents, workflows, integrations, and drive the REST API/SDK**
against a real backend, but you **cannot talk to an agent** (no telephony, no
in-dashboard test call). For any voice, self-host — see
[`deploy/hostinger/`](../hostinger/README.md) or the
[Docker guide](https://docs.dograh.com/deployment/docker).

## Object storage is external (bring your own S3)

Render has no managed object store, so the Blueprint points Dograh at an external
S3-compatible bucket rather than bundling MinIO (which needs a paid disk and a
public URL to serve audio). **Supabase Storage** has a free S3-compatible tier
that works well. During deploy Render prompts for the `sync: false` values:

| Variable | Value |
|---|---|
| `S3_BUCKET` | your bucket name |
| `S3_REGION` | bucket region (Supabase: your project region, e.g. `us-east-1`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 access keys |
| `S3_ENDPOINT_URL` | non-AWS endpoint, e.g. `https://<project>.supabase.co/storage/v1/s3` |

`S3_SIGNATURE_VERSION=s3v4` and `S3_ADDRESSING_STYLE=path` are pre-set (required
by Supabase and most non-AWS S3 servers).

## One post-deploy step

A Blueprint can't self-reference a service's public URL, so after the first
deploy set **`PUBLIC_BASE_URL`** on `dograh-api` to its own `*.onrender.com` URL,
then redeploy. This is **required, not optional** — the browser dashboard reads
the API's public URL from here (`backend_api_endpoint`), and if it's unset the SDK
falls back to `http://localhost:8000`, so every browser API call would hit the
user's own machine instead of the API. It's also used for public links and inbound
webhook signatures.

## Notes

- The **api needs ≥ 2 GB RAM** (Standard). It runs the whole monolith — uvicorn,
  an ARQ worker, the telephony/campaign singletons, and pipecat — in one
  container, so the **free/starter tiers (512 MB) OOM**. The Blueprint sets
  `dograh-api` to `standard` for this reason; `dograh-ui` fits on free.
- Prebuilt-image services **deploy manually** on Render (no auto-deploy on a new
  image push) — trigger a redeploy from the dashboard to pick up a new version.
