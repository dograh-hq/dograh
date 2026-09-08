---
title: CALMOS Data Explorer security
description: Security boundaries for sensitive call and memory data.
---

# CALMOS Data Explorer security

The Explorer denies API access without its administrator bearer credential. It
does not rely on an obscure URL, exposes no write endpoints, has no arbitrary
SQL UI, and uses static parameterized SELECT statements only. Deploy it behind
restricted admin access and replace/rotate the local bootstrap token through a
secret manager before multi-user production use.

The service writes privacy-safe audit metadata (actor, event type, time, and
resource identifiers) to its separate audit sink. It never logs transcript or
memory bodies. Browser downloads are either proxied from an authenticated API
or issued as server-generated, short-lived presigned URLs; permanent S3 URLs
and binary audio are excluded from JSON exports.

Run `/api/read-only-status` after provisioning. It reports PostgreSQL
`transaction_read_only` and the configured role’s INSERT/UPDATE/DELETE/CREATE
privilege evidence. A passing production deployment must report `on` and false
for every mutation capability.
