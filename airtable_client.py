"""
Creates and updates support tickets in Airtable (Step 6 of the spec).

SQLite remains the system of record for the audit trail and for offline
operation, but when AIRTABLE_API_KEY / AIRTABLE_BASE_ID / AIRTABLE_TABLE_NAME
are configured, every ticket is also mirrored into an Airtable base so support
agents can work from Airtable's UI. The Airtable record id is stored back on
the local ticket row (airtable_record_id) so later updates PATCH the same row
instead of creating duplicates.

If Airtable is not configured, all functions are no-ops that return None -
the rest of the pipeline is unaffected.
"""
import json
from typing import Any, Dict, Optional

import requests

from config import settings
from logging_config import get_logger

logger = get_logger("airtable_client")

API_ROOT = "https://api.airtable.com/v0"


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.airtable_api_key}",
        "Content-Type": "application/json",
    }


def _table_url() -> str:
    return f"{API_ROOT}/{settings.airtable_base_id}/{settings.airtable_table_name}"


def _to_airtable_fields(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """Maps our internal ticket dict onto Airtable field names. Tags are
    flattened to a comma-separated string and long text fields are trimmed to
    keep well within Airtable's per-field limits."""
    tags = ticket.get("suggested_tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [tags]
    tags = tags or []

    return {
        "Ticket ID": ticket.get("ticket_id"),
        "Customer Name": ticket.get("customer_name") or "",
        "Company": ticket.get("company") or "",
        "Sender Email": ticket.get("sender_email") or "",
        "Email Subject": ticket.get("email_subject") or "",
        "Issue Summary": ticket.get("issue_summary") or "",
        "Detailed Description": (ticket.get("detailed_description") or "")[:9000],
        "Category": ticket.get("category") or "",
        "Priority": ticket.get("priority") or "",
        "Priority Reason": ticket.get("priority_reason") or "",
        "Sentiment": ticket.get("sentiment") or "",
        "Product/Service": ticket.get("product_service") or "",
        "Suggested Department": ticket.get("suggested_department") or "",
        "Tags": ", ".join(tags),
        "Confidence Score": ticket.get("confidence_score") or 0,
        "Assigned Team": ticket.get("assigned_team") or "",
        "Status": ticket.get("status") or "Open",
        "Internal Notes": ticket.get("internal_notes") or "",
        "Duplicate Of": ticket.get("duplicate_of") or "",
        "Received At": ticket.get("received_at") or "",
        "Last Updated": ticket.get("last_updated") or "",
    }


def create_ticket(ticket: Dict[str, Any]) -> Optional[str]:
    """Creates a record in Airtable and returns its record id, or None if
    Airtable isn't configured or the request fails."""
    if not settings.airtable_enabled:
        return None
    try:
        resp = requests.post(
            _table_url(),
            headers=_headers(),
            json={"fields": _to_airtable_fields(ticket)},
            timeout=15,
        )
        resp.raise_for_status()
        record_id = resp.json().get("id")
        logger.info("Created Airtable record %s for ticket %s", record_id, ticket.get("ticket_id"))
        return record_id
    except Exception as e:
        logger.error("Airtable create failed for %s: %s", ticket.get("ticket_id"), e)
        return None


def update_ticket(record_id: str, ticket: Dict[str, Any]) -> bool:
    """Patches an existing Airtable record with the current ticket state."""
    if not settings.airtable_enabled or not record_id:
        return False
    try:
        resp = requests.patch(
            f"{_table_url()}/{record_id}",
            headers=_headers(),
            json={"fields": _to_airtable_fields(ticket)},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Updated Airtable record %s", record_id)
        return True
    except Exception as e:
        logger.error("Airtable update failed for record %s: %s", record_id, e)
        return False
