# Dockerfile for Dograh self-hosted Asterisk + Verimor ARI overlay
# Guarantees required modules are loaded (fail-fast if missing)
#
# Build: docker compose -f deploy/asterisk/docker-compose.asterisk.yaml build
# Run:   docker compose -f deploy/asterisk/docker-compose.asterisk.yaml up -d
#
# Base image: andrius/asterisk:${ASTERISK_VERSION}
#   - Confirmed pullable on linux/arm64 (aarch64), Asterisk 22.10.1
#   - Ships all required ARI/PJSIP/WebSocket/codec modules (see modules.conf)
#   - `asterisk:22` (Docker Hub) does NOT exist (pull access denied), so we
#     must use this community image. Switch only after verifying an alternative
#     ships every module in modules.conf on aarch64.

ARG ASTERISK_VERSION=22
FROM andrius/asterisk:${ASTERISK_VERSION}

# Set working directory
WORKDIR /root

# Copy configuration templates and modules list into image
COPY conf/*.template /etc/asterisk/templates/
COPY conf/modules.conf /etc/asterisk/
COPY conf/rtp.conf /etc/asterisk/

# Copy entrypoint wrapper and config-rendering script, make executable.
#
# The andrius/asterisk:22 base image's ENTRYPOINT is
# /usr/local/bin/entrypoint.sh, which does uid/chown setup and then
# execs asterisk — but it does NOT source /docker-entrypoint.d/*.sh
# (unlike the standard docker-entrypoint.sh from the library/bash image).
#
# Our wrapper (entrypoint-wrapper.sh) sources every /docker-entrypoint.d/*.sh
# script first — including our 00-render-asterisk config-rendering script —
# then delegates to the base entrypoint for the actual asterisk launch.
COPY entrypoint.sh /docker-entrypoint.d/00-render-asterisk
COPY entrypoint-wrapper.sh /usr/local/bin/
RUN chmod +x /docker-entrypoint.d/00-render-asterisk /usr/local/bin/entrypoint-wrapper.sh

# Override entrypoint to wrap the base image's entrypoint and source
# /docker-entrypoint.d/*.sh scripts before starting Asterisk.
ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]

# Explicitly set CMD so compose passes it as args to our ENTRYPOINT.
# These are the same flags the andrius/asterisk:22 base image uses.
CMD ["/usr/sbin/asterisk", "-vvvdddf", "-T", "-W", "-U", "asterisk", "-p"]

# Expose SIP signaling and bounded RTP range for Verimor
EXPOSE 5060/udp
EXPOSE 10000-10100/udp
