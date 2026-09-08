---
title: CALMOS Data Explorer AWS deployment plan
description: Future-only deployment design; no AWS resources are created by this change.
---

# CALMOS Data Explorer AWS deployment plan

No AWS resource, IAM policy, image registry, RDS instance, S3 bucket, or
production data is changed by this implementation.

Future deployment should place the service behind VPN, private admin ingress or
SSO-aware restricted ingress. Give its ECS task/EC2 instance role only
`s3:GetObject` on the discovered artifact bucket and necessary prefixes; add
`s3:ListBucket` only if live object listing is later required (it is not needed
for the current key-backed Explorer). If a customer-managed KMS key encrypts
objects, add scoped `kms:Decrypt` for that key. The service uses the SDK default
credential chain, so no static IAM user key is required.

Use a separate RDS/PostgreSQL principal with `SELECT` only. Keep S3 Block Public
Access enabled. Presigned GETs default to 300 seconds and are generated only
after Explorer authorization. Route audit logs to a separate protected log
destination, never `audit_events` in the read-only clinical datastore.
