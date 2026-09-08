---
title: CALMOS Data Explorer local setup
description: Run the isolated read-only explorer locally.
---

# CALMOS Data Explorer local setup

Create a dedicated PostgreSQL role with `CONNECT`, schema `USAGE`, and `SELECT`
only on the audited CALMOS tables. Do not use Dograh’s normal application URL.
Copy `apps/data-explorer/.env.example` to an uncommitted local env file, set a
random admin token, and provide the dedicated read-only URL.

```bash
docker compose -f docker-compose.data-explorer.yaml --env-file apps/data-explorer/.env up --build
```

Open `http://localhost:8090`, then enter the configured administrator token.
For direct development, install the app dependencies and run:

```bash
cd apps/data-explorer
uvicorn data_explorer.main:app --reload --port 8090
```

For AWS-backed storage use AWS CLI profile or SSO (`AWS_PROFILE`); do not put
AWS access keys in `.env`. Local object-store access must target a private
compatible endpoint and use local-only credentials outside source control.
