"""Lead-notification email delivery (SMTP, stdlib only).

The public lead forms (Hire an Expert, Enterprise, post-signup Onboarding)
POST to ``/api/v1/leads/*``; those thin route handlers delegate here to email
the submission to the deployment owner.

Configuration is entirely environment-driven (see ``api/.env.example``):

  LEAD_NOTIFICATION_EMAIL  Destination inbox. Default: hardikagarwal@autosysai.dev
  SMTP_HOST                SMTP server hostname (required to actually send)
  SMTP_PORT                SMTP port. Default: 587 (STARTTLS)
  SMTP_USER                SMTP auth username (required to actually send)
  SMTP_PASSWORD            SMTP auth password (required to actually send)
  SMTP_FROM                From address. Default: SMTP_USER
  SMTP_STARTTLS            "false" to disable STARTTLS. Default: enabled
  SMTP_SSL                 "true" to use implicit TLS (SMTPS, e.g. port 465)

Delivery is BEST-EFFORT. If the required SMTP env vars are missing, or the send
fails, we log a clear warning/error and return ``False`` — we never raise. The
route still returns success so the user's form submission is never blocked; the
log makes it obvious that email delivery is unconfigured or failing.
"""

import asyncio
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any, Mapping

from loguru import logger

DEFAULT_LEAD_EMAIL = "hardikagarwal@autosysai.dev"

# Human-friendly labels for the lead "kind" sent by the frontend.
_KIND_LABELS = {
    "hire_expert": "Hire an Expert",
    "enterprise": "Enterprise / Strategy Call",
    "onboarding": "Onboarding",
}


def _destination() -> str:
    """Resolve the notification inbox, defaulting to the Auto4You owner."""
    return (os.getenv("LEAD_NOTIFICATION_EMAIL") or DEFAULT_LEAD_EMAIL).strip()


def _smtp_config() -> dict[str, Any] | None:
    """Return SMTP settings, or ``None`` when delivery is not configured.

    Host / user / password are all required to send; everything else has a
    sensible default.
    """
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and user and password):
        return None

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        port = 587

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_addr": (os.getenv("SMTP_FROM") or user).strip(),
        "use_ssl": os.getenv("SMTP_SSL", "false").lower() == "true",
        "use_starttls": os.getenv("SMTP_STARTTLS", "true").lower() != "false",
    }


def _build_message(kind: str, payload: Mapping[str, Any], cfg: dict[str, Any], to_addr: str) -> EmailMessage:
    label = _KIND_LABELS.get(kind, kind or "Lead")
    country = str(payload.get("country") or "").strip()
    contact = str(payload.get("email") or payload.get("workEmail") or "unknown").strip()

    subject = f"New {label} lead"
    if country:
        subject += f" — {country}"
    subject += f" ({contact})"

    # Render the payload as a readable key: value list (skip empties).
    lines = [f"A new {label} lead was submitted.", ""]
    for key in sorted(payload.keys()):
        value = payload[key]
        if value is None or value == "":
            continue
        lines.append(f"{key}: {value}")

    body = "\n".join(lines) + "\n"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_addr
    if contact and contact != "unknown":
        msg["Reply-To"] = contact
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    return msg


def _send_sync(kind: str, payload: Mapping[str, Any], cfg: dict[str, Any], to_addr: str) -> None:
    """Blocking SMTP send. Runs in a worker thread via ``asyncio.to_thread``."""
    msg = _build_message(kind, payload, cfg, to_addr)

    if cfg["use_ssl"]:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
    try:
        server.ehlo()
        if cfg["use_starttls"] and not cfg["use_ssl"]:
            server.starttls()
            server.ehlo()
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


async def send_lead_notification(kind: str, payload: Mapping[str, Any]) -> bool:
    """Email a lead submission to the deployment owner. Best-effort.

    Returns ``True`` when an email was actually handed off to the SMTP server,
    ``False`` when delivery was skipped (unconfigured) or failed. Never raises.
    """
    to_addr = _destination()
    cfg = _smtp_config()

    if cfg is None:
        logger.warning(
            "Lead notification email NOT sent (SMTP unconfigured) — set "
            "SMTP_HOST/SMTP_USER/SMTP_PASSWORD to enable delivery. "
            "kind={} destination={}",
            kind,
            to_addr,
        )
        return False

    try:
        await asyncio.to_thread(_send_sync, kind, payload, cfg, to_addr)
        logger.info("Lead notification email sent. kind={} destination={}", kind, to_addr)
        return True
    except Exception as exc:  # noqa: BLE001 — never block the form on email failure
        logger.error(
            "Lead notification email FAILED to send. kind={} destination={} error={}",
            kind,
            to_addr,
            exc,
        )
        return False
