"""Generic SMTP client for outbound email (contact-author + password reset).

Reads `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_SENDER_EMAIL`, and `SMTP_SECURITY` (starttls / ssl / none).
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

logger = logging.getLogger(__name__)

_VALID_SECURITY = {"starttls", "ssl", "none"}


class SMTPNotConfigured(RuntimeError):
    """No SMTP credentials present in the environment."""


def _load_smtp_config() -> dict:
    host = os.getenv("SMTP_HOST", "").strip()
    port_raw = os.getenv("SMTP_PORT", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("SMTP_SENDER_EMAIL", "").strip()
    security = os.getenv("SMTP_SECURITY", "").strip().lower()

    if not (host and username and password and sender):
        raise SMTPNotConfigured(
            "Email service not configured. Set SMTP_HOST + SMTP_USERNAME + "
            "SMTP_PASSWORD + SMTP_SENDER_EMAIL."
        )

    if not security:
        security = "starttls"
    if security not in _VALID_SECURITY:
        raise ValueError(
            f"Invalid SMTP_SECURITY={security!r}. Expected one of: "
            f"{', '.join(sorted(_VALID_SECURITY))}."
        )

    port = int(port_raw) if port_raw else (465 if security == "ssl" else 587)

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "sender": sender,
        "security": security,
    }


def send_email(
    *,
    recipients: List[str],
    subject: str,
    body: str,
    body_type: str = "plain",
    timeout: int = 15,
) -> str:
    """Send an email via the configured SMTP server.

    Returns the sender address. Raises ``SMTPNotConfigured`` when env vars are
    missing, or ``smtplib.SMTPException`` for transport / auth errors.
    """
    config = _load_smtp_config()

    msg = MIMEMultipart()
    msg["From"] = config["sender"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, body_type))

    security = config["security"]
    if security == "ssl":
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=timeout) as server:
            server.login(config["username"], config["password"])
            server.send_message(msg)
    elif security == "starttls":
        with smtplib.SMTP(config["host"], config["port"], timeout=timeout) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config["username"], config["password"])
            server.send_message(msg)
    else:  # "none" — for internal relays that don't require auth/TLS
        with smtplib.SMTP(config["host"], config["port"], timeout=timeout) as server:
            server.ehlo()
            if config["password"]:
                server.login(config["username"], config["password"])
            server.send_message(msg)

    return config["sender"]
