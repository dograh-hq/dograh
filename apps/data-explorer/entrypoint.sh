#!/bin/sh
set -eu

# Named volumes are created root-owned by Docker. Give only the separate audit
# destination to the unprivileged runtime user before starting the service.
mkdir -p /var/log/calmos-data-explorer
chown -R explorer:explorer /var/log/calmos-data-explorer
exec runuser -u explorer -- "$@"
