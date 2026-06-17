# 3CX Telephony Provider

Connect a Dograh AI agent to a **3CX cloud PBX** through an intermediate
**Asterisk** bridge. The Asterisk box terminates the SIP/RTP leg toward
3CX and exposes a standard ARI + externalMedia surface to Dograh —
identical to the [Asterisk ARI provider](../integrations/telephony/asterisk-ari)
plus an automated trunk-provisioning step.

```
+-------------------+    SIP/RTP     +-------------+   ARI REST +    +---------+
|   3CX cloud SBC   | <------------> |  Asterisk   |   WS audio | -> |  Dograh |
| 1156.3cx.cloud    |                |  (PJSIP ARA)|            |    |  agent  |
+-------------------+                +-------------+            +    +---------+
                                            ^
                                            |  ps_endpoints, ps_aors,
                                            |  ps_auths, ps_registrations,
                                            |  extensions  (Postgres)
                                            |
                                       +----+----+
                                       | Dograh  |
                                       | save UI |
                                       +---------+
```

When an admin saves a 3CX TelephonyConfiguration in the Dograh UI, the
provider's `preprocess_credentials_on_save` hook writes the matching
PJSIP endpoint/aor/auth/registration rows and the `+39`-stripping
dialplan into the Asterisk Realtime Architecture (ARA) Postgres. Asterisk
picks them up dynamically — no `pjsip reload` needed.

## §1 — Asterisk side prerequisites

The bridging Asterisk must be ≥ Asterisk 18 with PJSIP and ARA enabled
against the same Postgres Dograh writes to. One Asterisk instance can
serve many Dograh 3CX configurations (multi-tenant) because each trunk
gets a unique endpoint id of the form `dograh_<slug(sip_domain)>_<extension>`.

### 1.1 Postgres tables

Run the standard Asterisk realtime DDL on the ARA database — the
relevant tables are `ps_auths`, `ps_aors`, `ps_endpoints`,
`ps_registrations`, `ps_transports`, `ps_globals`, and `extensions`.
The canonical schema ships with Asterisk under `contrib/realtime/postgresql/`.

### 1.2 `res_config_pgsql.conf`

```ini
[asterisk]
type = pgsql
hostname = postgres.internal
dbname = asterisk_ara
user = asterisk_ro
password = ********
port = 5432
requirements = warn
```

### 1.3 `sorcery.conf`

```ini
[res_pjsip]
endpoint = realtime,ps_endpoints
auth = realtime,ps_auths
aor = realtime,ps_aors
domain_alias = realtime,ps_domain_aliases
contact = realtime,ps_contacts

[res_pjsip_endpoint_identifier_ip]
identify = realtime,ps_endpoint_id_ips

[res_pjsip_outbound_registration]
registration = realtime,ps_registrations
```

### 1.4 `extconfig.conf`

```ini
[settings]
ps_endpoints = pgsql,asterisk
ps_auths = pgsql,asterisk
ps_aors = pgsql,asterisk
ps_registrations = pgsql,asterisk
extensions = pgsql,asterisk
```

### 1.5 Static PJSIP transport

Dograh writes endpoints that reference a transport by name (default:
`transport-udp`). Define it once in `pjsip.conf`:

```ini
[transport-udp]
type = transport
protocol = udp
bind = 0.0.0.0:5060
```

### 1.6 Stasis app + externalMedia

```ini
; ari.conf
[general]
enabled = yes
[dograh]
type = user
read_only = no
password = <ARI password to paste in the Dograh UI>

; websocket_client.conf
[dograh_staging]
type = websocket_client
uri = ws://dograh-backend:8000/api/v1/telephony/ws/ari
protocols = media
connection_type = persistent
```

Start the Stasis app and confirm registration is happening:

```bash
asterisk -rx "module reload res_pjsip.so"
asterisk -rx "pjsip show registrations"
```

## §2 — Dograh side prerequisites

Set the connection string to the ARA Postgres in `api/.env`:

```bash
ASTERISK_ARA_DSN=postgresql://dograh_rw:********@postgres.internal:5432/asterisk_ara
```

The user needs `SELECT, INSERT, UPDATE, DELETE` on `ps_auths`,
`ps_aors`, `ps_endpoints`, `ps_registrations`, and `extensions`. No DDL
permissions required at runtime.

Restart the Dograh API process after setting the env var.

## §3 — Per-trunk flow in the Dograh UI

For each 3CX tenant + extension Dograh should serve:

1. Open *Telephony Configurations* → *Add* → select **3CX (Asterisk bridge)**.
2. Fill in the form:

   | Field | Value (example) |
   | --- | --- |
   | ARI Endpoint | `http://asterisk.internal:8088` |
   | Stasis App Name | `dograh` |
   | ARI Password | _(matches `ari.conf` `[dograh]` password)_ |
   | websocket_client.conf Name | `dograh_staging` |
   | 3CX SIP Domain | `1156.3cx.cloud` |
   | 3CX Extension | `12611` |
   | SIP Password | _(from `~/.claude-phone/.env` or 3CX admin console)_ |
   | Strip Prefix (regex) | `^\+39` |
   | From Numbers | `+393331112222` |

3. Save. On save the `preprocess_credentials_on_save` hook writes the
   six-table ARA set in a single transaction. A failure aborts the save
   with `HTTP 502` and a message describing which write failed; nothing
   persists.

## §4 — Verification

Confirm the trunk landed in ARA:

```bash
psql $ASTERISK_ARA_DSN -c \
  "SELECT id FROM ps_endpoints WHERE id LIKE 'dograh\\_%'"
psql $ASTERISK_ARA_DSN -c \
  "SELECT id, server_uri FROM ps_registrations WHERE id LIKE 'dograh\\_%'"
```

Confirm Asterisk has registered upstream with 3CX:

```bash
asterisk -rx "pjsip show registrations"
# Expect: <id> <server>  Registered
```

Originate a test outbound call from Dograh and verify the `+39` prefix
was stripped on the way out:

```bash
asterisk -rx "core set verbose 4"
# In another terminal: trigger an outbound from the Dograh API.
# In the Asterisk console you should see:
#   Dial: PJSIP/3331112222@dograh_1156_3cx_cloud_12611
# i.e. without '+39'.
```

## Known limitations

* The hook **only** supports the literal `^\+<digits>` regex form for
  `strip_prefix`. PCRE alternation isn't translated to Asterisk's
  ad-hoc extension pattern syntax. Adding a `[02-9]` or branching
  regex needs an extension to `dialplan._prefix_to_pattern`.
* Deprovisioning on TelephonyConfiguration deletion is not currently
  wired. `provisioning._deprovision_3cx_trunk` exists as a callable but
  no registry hook fires it; admin tooling can call it directly. Filed
  for follow-up rather than in scope for the initial provider.
* `transport_name` is hard-coded to `transport-udp` (overridable per
  credentials dict). TLS or TCP trunks toward 3CX need the admin to
  define the transport and pass the name through.
