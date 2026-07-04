"""
Monitors a real mailbox over IMAP for new customer support emails.

This satisfies "Step 1 - Monitor Support Inbox" from the spec:
  - detects new (unseen) emails
  - captures metadata (sender name, sender email, subject, received time)
  - extracts the plain-text body
  - saves any attachments to disk

If IMAP is not configured (settings.imap_enabled is False), fetch_unseen_emails()
returns an empty list rather than raising, so the app still works in a pure
manual-entry / sample-data demo mode.
"""
import email
import imaplib
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List

from config import settings
from logging_config import get_logger

logger = get_logger("email_reader")


def _decode(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        # Prefer text/plain; fall back to a stripped text/html part.
        plain, html = None, None
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and plain is None:
                plain = part.get_payload(decode=True)
            elif content_type == "text/html" and html is None:
                html = part.get_payload(decode=True)
        raw = plain or html or b""
        charset = "utf-8"
        return raw.decode(charset, errors="ignore").strip()
    else:
        raw = msg.get_payload(decode=True) or b""
        return raw.decode("utf-8", errors="ignore").strip()


def _save_attachments(msg: email.message.Message, ticket_hint: str) -> List[str]:
    saved = []
    if not msg.is_multipart():
        return saved
    attachments_dir = Path(settings.attachments_dir)
    attachments_dir.mkdir(parents=True, exist_ok=True)
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        filename = part.get_filename()
        if "attachment" in disposition and filename:
            filename = _decode(filename)
            safe_name = f"{ticket_hint}_{filename}".replace("/", "_").replace("\\", "_")
            target = attachments_dir / safe_name
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    target.write_bytes(payload)
                    saved.append(str(target))
            except Exception as e:
                logger.error("Failed to save attachment %s: %s", filename, e)
    return saved


def fetch_unseen_emails(limit: int = 20) -> List[Dict[str, Any]]:
    """Connects to the configured IMAP mailbox and returns a list of parsed
    email dicts ready to be fed into the ticket workflow. Marks messages as
    seen once processed (configurable via IMAP_MARK_SEEN)."""
    if not settings.imap_enabled:
        logger.info("IMAP not configured - skipping inbox poll")
        return []

    results: List[Dict[str, Any]] = []
    try:
        imap = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        imap.login(settings.imap_username, settings.imap_password)
        imap.select(settings.imap_folder)

        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            logger.warning("IMAP search failed with status %s", status)
            imap.logout()
            return []

        message_ids = data[0].split()[-limit:]
        for msg_id in message_ids:
            fetch_mode = "(RFC822)" if settings.imap_mark_seen else "(BODY.PEEK[])"
            status, msg_data = imap.fetch(msg_id, fetch_mode)
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            sender_name, sender_email = parseaddr(_decode(msg.get("From", "")))
            subject = _decode(msg.get("Subject", "(no subject)"))
            try:
                received_at = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
            except Exception:
                received_at = datetime.now(timezone.utc).isoformat()

            body = _extract_body(msg)
            hint = sender_email.split("@")[0] if sender_email else "ticket"
            attachments = _save_attachments(msg, hint)

            results.append({
                "sender_name": sender_name or sender_email,
                "sender_email": sender_email,
                "subject": subject,
                "body": body,
                "received_at": received_at,
                "attachments": attachments,
            })

        imap.logout()
        logger.info("Fetched %d new email(s) from %s", len(results), settings.imap_host)
    except Exception as e:
        logger.error("IMAP polling failed: %s", e)

    return results
