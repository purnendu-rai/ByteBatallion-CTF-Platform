"""Transactional email via the Brevo HTTPS API (v3.5).

Why an HTTPS API and not SMTP: Render's free web services block
outbound SMTP ports 25, 465 and 587 (since Sept 2025), so a normal
SMTP setup hangs and times out there no matter how correct the
credentials are. The Brevo API runs over HTTPS (port 443), which is
not blocked on any Render plan.

Free tier: Brevo sends 300 emails/day with no card. The sender is a
plain verified email address (no own domain needed) — set EMAIL_FROM
to it; the API key lives in BREVO_API_KEY (Render dashboard env var,
never in the repo). When the key is unset, sending is a logged no-op
and returns False so callers can tell the user to contact the admin
instead of crashing the request.
"""

import logging
import re

import httpx

from app.config import get_settings

_logger = logging.getLogger("bytebatallion.email")

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def is_configured() -> bool:
    """True when a Brevo API key is set — i.e. mail can actually go out.
    Auth gates email verification on this (see routes/auth.py): with no
    key configured, registration keeps its old behaviour (account usable
    immediately) instead of creating accounts nobody can verify."""
    return bool((get_settings().brevo_api_key or "").strip())

_FROM_RE = re.compile(r"^(?:(?P<name>[^<>]+)\s*)?<(?P<email>[^<>@\s]+@[^<>@\s]+)>$")


def _sender() -> dict:
    """Parse EMAIL_FROM ("Name <addr@example.com>" or plain address)."""
    raw = (get_settings().email_from or "").strip()
    m = _FROM_RE.match(raw)
    if m:
        sender = {"email": m.group("email")}
        name = (m.group("name") or "").strip().strip('"')
        if name:
            sender["name"] = name
        return sender
    return {"email": raw or "noreply@bytebatallion.local"}


def send_email(to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Send one transactional email. True on acceptance (2xx), else False."""
    settings = get_settings()
    if not settings.brevo_api_key:
        _logger.warning("Email not configured (BREVO_API_KEY unset) — not sending to %s", to_email)
        return False
    payload = {
        "sender": _sender(),
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body or re.sub(r"<[^>]+>", "", html_body),
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                BREVO_API_URL,
                json=payload,
                headers={
                    "api-key": settings.brevo_api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
    except Exception:  # noqa: BLE001 — network failure must not break the request
        _logger.exception("Brevo request failed for %s", to_email)
        return False
    if resp.status_code in (200, 201):
        return True
    _logger.warning("Brevo rejected mail to %s: %s %s", to_email, resp.status_code, resp.text[:300])
    return False


def send_verification_email(to_email: str, username: str, link: str) -> bool:
    """Account-verification mail (link valid 24h)."""
    html = (
        f"<p>Hi {username},</p>"
        "<p>Welcome to <b>BYTE_BATALLION_CTF_PLATFORM</b>! Click the link below "
        "to verify your email address and activate your account:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        "<p>This link expires in 24 hours. If you did not create this account, "
        "just ignore this email.</p>"
        "<p>— ByteBatallion</p>"
    )
    return send_email(to_email, "Verify your ByteBatallion account", html)


def send_password_reset_email(to_email: str, username: str, link: str) -> bool:
    """Password-reset mail (link valid 1h, single-use)."""
    html = (
        f"<p>Hi {username},</p>"
        "<p>Someone asked to reset the password for your "
        "<b>BYTE_BATALLION_CTF_PLATFORM</b> account. Click the link below "
        "to choose a new password:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        "<p>This link expires in 1 hour and stops working once your password "
        "has been changed. If you did not ask for this, just ignore this "
        "email — your password stays the same.</p>"
        "<p>— ByteBatallion</p>"
    )
    return send_email(to_email, "Reset your ByteBatallion password", html)
