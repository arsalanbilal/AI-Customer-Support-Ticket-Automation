"""
Core LangGraph pipeline: analyze -> validate -> check_duplicate -> persist ->
sync_external -> acknowledge.

Fixes applied vs. the previous version:
  - Fixed a fatal IndentationError in the `ack` node that prevented the module
    from even being imported.
  - The AI analysis step now actually uses the SYSTEM_PROMPT / ANALYSIS_PROMPT
    from prompts.py (previously they were defined but never sent to the LLM).
  - Added retry-with-backoff around the LLM call instead of failing silently
    on the first error.
  - Added duplicate-ticket detection.
  - Added Airtable sync (create + update) so tickets are pushed to a real
    ticketing platform, not just SQLite.
  - Category -> team routing is now loaded from team_mapping.json instead of
    being hard-coded, so it can be reconfigured without touching code.
  - Everything meaningful is logged via logging_config.
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from email_validator import validate_email, EmailNotValidError
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from rapidfuzz import process, fuzz

import airtable_client
from config import settings
from email_sender import EmailSender
from logging_config import get_logger
from prompts import SYSTEM_PROMPT, ANALYSIS_PROMPT_TEMPLATE

logger = get_logger("ticket_workflow")

CATEGORIES = [
    "Technical Support", "Billing", "Sales Inquiry", "Feature Request",
    "Bug Report", "Account Access", "Refund Request", "General Inquiry",
]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
SENTIMENTS = ["Positive", "Neutral", "Negative"]

TEAM_MAPPING = settings.load_team_mapping()


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class EmailInput(BaseModel):
    sender_name: str = ""
    sender_email: str
    subject: str
    body: str
    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attachments: List[str] = Field(default_factory=list)


class TicketState(BaseModel):
    raw_email: Dict[str, Any]
    analysis: Dict[str, Any] = Field(default_factory=dict)
    ticket: Dict[str, Any] = Field(default_factory=dict)
    ack_email: str = ""
    errors: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(text: str) -> Dict[str, Any]:
    """Parses AI output into a dict, tolerating markdown fences and stray
    text around the JSON payload. Raises if no JSON object can be found at
    all, which the caller treats as an analysis failure."""
    text = (text or "").strip()
    text = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


def normalize_category(value: Optional[str]) -> str:
    if not value:
        return "General Inquiry"
    match = process.extractOne(str(value), CATEGORIES, scorer=fuzz.WRatio)
    return match[0] if match and match[1] >= 65 else "General Inquiry"


def normalize_priority(value: Optional[str]) -> str:
    if not value:
        return "Medium"
    v = str(value).strip().title()
    return v if v in PRIORITIES else "Medium"


def normalize_sentiment(value: Optional[str]) -> str:
    if not value:
        return "Neutral"
    v = str(value).strip().title()
    return v if v in SENTIMENTS else "Neutral"


def dedupe_tags(tags) -> List[str]:
    out, seen = [], set()
    for t in tags or []:
        t = re.sub(r"\s+", "-", str(t).strip().lower())
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def validate_sender(email_address: str) -> str:
    try:
        return validate_email(email_address, check_deliverability=False).normalized
    except EmailNotValidError:
        return (email_address or "").strip().lower()


def assign_team(category: str) -> str:
    return TEAM_MAPPING.get(category, "Customer Success")


def priority_from_rules(category: str, body: str, ai_priority: Optional[str]) -> Tuple[str, str]:
    """Blends AI-suggested priority with deterministic business rules so that
    known urgent-keyword patterns are never under-prioritized by the model."""
    txt = (body or "").lower()
    critical_kw = ["outage", "down", "broken in production", "production down", "data loss", "security breach"]
    high_kw = ["unable to log in", "cannot log in", "payment failed", "charged twice", "urgent", "asap"]

    if any(k in txt for k in critical_kw):
        return "Critical", "Business rule: production-impacting / data-loss keywords detected"
    if category == "Account Access" and any(k in txt for k in high_kw):
        return "High", "Business rule: account access issue with urgent language"
    if category == "Refund Request" and any(k in txt for k in ["dispute", "chargeback", "fraud"]):
        return "High", "Business rule: refund dispute requires fast review"
    if any(k in txt for k in high_kw):
        return "High", "Business rule: urgent language detected"

    ai_val = normalize_priority(ai_priority)
    return ai_val, "AI-assessed priority (no overriding business rule matched)"


def make_ticket_id() -> str:
    return f"TKT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:17]}"


def db_path() -> str:
    return settings.sqlite_path


def init_db() -> None:
    conn = sqlite3.connect(db_path())
    conn.executescript(Path(__file__).with_name("schema.sql").read_text())
    conn.commit()
    conn.close()


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def save_ticket(ticket: Dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path())
    cols = list(ticket.keys())
    vals = [ticket[c] for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO tickets ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.execute(
        "INSERT INTO audit_log(ticket_id, action, old_value, new_value, actor, timestamp) VALUES (?,?,?,?,?,?)",
        (ticket["ticket_id"], "create", "", json.dumps(ticket, default=str), "system", now_iso()),
    )
    conn.commit()
    conn.close()
    logger.info("Persisted ticket %s (category=%s, priority=%s, team=%s)",
                ticket["ticket_id"], ticket["category"], ticket["priority"], ticket["assigned_team"])


def get_ticket(ticket_id: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    row = cur.fetchone()
    result = _row_to_dict(cur, row) if row else None
    conn.close()
    return result


def list_tickets(limit: int = 100) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets ORDER BY received_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    result = [_row_to_dict(cur, r) for r in rows]
    conn.close()
    return result


def get_audit_log(ticket_id: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_log WHERE ticket_id=? ORDER BY id ASC", (ticket_id,))
    rows = cur.fetchall()
    result = [_row_to_dict(cur, r) for r in rows]
    conn.close()
    return result


def update_ticket(ticket_id: str, updates: Dict[str, Any], actor: str = "agent") -> Dict[str, Any]:
    """Agent-driven update supporting any subset of ticket fields (status,
    priority, category, assigned_team, internal_notes, etc.). Every call is
    recorded in audit_log and mirrored to Airtable if configured."""
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Ticket not found: {ticket_id}")

    before = _row_to_dict(cur, row)
    updates = dict(updates)
    updates["last_updated"] = now_iso()

    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
    values = list(updates.values()) + [ticket_id]
    conn.execute(f"UPDATE tickets SET {set_clause} WHERE ticket_id=?", values)
    conn.execute(
        "INSERT INTO audit_log(ticket_id, action, old_value, new_value, actor, timestamp) VALUES (?,?,?,?,?,?)",
        (ticket_id, "update", json.dumps(before, default=str), json.dumps(updates, default=str), actor, now_iso()),
    )
    conn.commit()

    cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    after = _row_to_dict(cur, cur.fetchone())
    conn.close()

    logger.info("Ticket %s updated by %s: %s", ticket_id, actor, list(updates.keys()))

    if after.get("airtable_record_id"):
        airtable_client.update_ticket(after["airtable_record_id"], after)

    return after


def find_duplicate(sender_email: str, issue_summary: str, category: str) -> Optional[str]:
    """Looks for a recent open ticket from the same sender with a highly
    similar issue_summary and returns its ticket_id, or None."""
    if not sender_email:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.duplicate_lookback_days)).isoformat()
    conn = sqlite3.connect(db_path())
    cur = conn.cursor()
    cur.execute(
        "SELECT ticket_id, issue_summary FROM tickets "
        "WHERE sender_email=? AND category=? AND received_at>=? AND status NOT IN ('Resolved','Closed')",
        (sender_email, category, cutoff),
    )
    rows = cur.fetchall()
    conn.close()

    for ticket_id, existing_summary in rows:
        score = fuzz.token_set_ratio(issue_summary or "", existing_summary or "")
        if score >= settings.duplicate_similarity_threshold:
            return ticket_id
    return None


def call_llm_with_retry(llm, prompt: str, system_prompt: str) -> str:
    """Invokes the LLM with basic exponential-backoff retry so transient API
    errors don't fail the whole ticket."""
    last_err = None
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            resp = llm.invoke([("system", system_prompt), ("human", prompt)])
            return getattr(resp, "content", str(resp))
        except Exception as e:
            last_err = e
            logger.warning("LLM call failed (attempt %d/%d): %s", attempt, settings.llm_max_retries, e)
            if attempt < settings.llm_max_retries:
                time.sleep(settings.llm_retry_backoff_seconds * attempt)
    raise RuntimeError(f"LLM call failed after {settings.llm_max_retries} attempts: {last_err}")


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #

def build_graph(llm):
    mailer = EmailSender()

    def analyze(state: TicketState) -> TicketState:
        raw = state.raw_email
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            sender_name=raw.get("sender_name", ""),
            sender_email=raw.get("sender_email", ""),
            received_at=raw.get("received_at", ""),
            subject=raw.get("subject", ""),
            body=raw.get("body", ""),
        )
        try:
            content = call_llm_with_retry(llm, prompt, SYSTEM_PROMPT)
            data = safe_json(content)
        except Exception as e:
            logger.error("AI analysis failed, falling back to defaults: %s", e)
            data = {}
            state.errors.append(f"analysis_failed:{e}")
        state.analysis = data
        return state

    def validate(state: TicketState) -> TicketState:
        raw = state.raw_email
        a = state.analysis or {}
        category = normalize_category(a.get("category"))
        priority, reason = priority_from_rules(category, raw.get("body", ""), a.get("priority"))

        state.ticket = {
            "ticket_id": make_ticket_id(),
            "customer_name": a.get("customer_name") or raw.get("sender_name") or "",
            "company": a.get("company") or "",
            "sender_email": validate_sender(raw["sender_email"]),
            "sender_name": raw.get("sender_name", ""),
            "email_subject": raw["subject"],
            "email_body": raw["body"],
            "issue_summary": a.get("issue_summary") or raw["subject"],
            "detailed_description": a.get("detailed_description") or raw["body"],
            "category": category,
            "priority": priority,
            "priority_reason": a.get("priority_reason") or reason,
            "sentiment": normalize_sentiment(a.get("sentiment")),
            "product_service": a.get("product_service") or "",
            "suggested_department": a.get("suggested_department") or assign_team(category),
            "suggested_tags": json.dumps(dedupe_tags(a.get("suggested_tags") or [])),
            "confidence_score": float(a.get("confidence_score") or 0.5),
            "assigned_team": assign_team(category),
            "status": "Open",
            "internal_notes": "",
            "attachments": json.dumps(raw.get("attachments", [])),
            "duplicate_of": "",
            "airtable_record_id": "",
            "original_email_json": json.dumps(raw, default=str),
            "received_at": raw.get("received_at") or now_iso(),
            "last_updated": now_iso(),
        }
        if state.errors:
            state.ticket["internal_notes"] = "AI analysis had issues - please double-check classification. " + "; ".join(state.errors)
        return state

    def check_duplicate(state: TicketState) -> TicketState:
        t = state.ticket
        dup_id = find_duplicate(t["sender_email"], t["issue_summary"], t["category"])
        if dup_id:
            t["duplicate_of"] = dup_id
            logger.info("Ticket %s flagged as possible duplicate of %s", t["ticket_id"], dup_id)
        return state

    def persist(state: TicketState) -> TicketState:
        save_ticket(state.ticket)
        return state

    def sync_external(state: TicketState) -> TicketState:
        record_id = airtable_client.create_ticket(state.ticket)
        if record_id:
            state.ticket["airtable_record_id"] = record_id
            try:
                conn = sqlite3.connect(db_path())
                conn.execute(
                    "UPDATE tickets SET airtable_record_id=? WHERE ticket_id=?",
                    (record_id, state.ticket["ticket_id"]),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to save airtable_record_id locally: %s", e)
        return state

    def ack(state: TicketState) -> TicketState:
        t = state.ticket
        state.ack_email = (
            f"Ticket {t['ticket_id']} received. Summary: {t['issue_summary']}. "
            f"Status: {t['status']}. Assigned to: {t['assigned_team']}."
        )
        if settings.auto_send_ack:
            result = mailer.send_ack(
                to_email=t["sender_email"],
                ticket_id=t["ticket_id"],
                issue_summary=t["issue_summary"],
                customer_name=t["customer_name"],
                assigned_team=t["assigned_team"],
                status=t["status"],
            )
            if not result["ok"]:
                state.errors.append(f"smtp:{result['message']}")
        return state

    g = StateGraph(TicketState)
    g.add_node("analyze", analyze)
    g.add_node("validate", validate)
    g.add_node("check_duplicate", check_duplicate)
    g.add_node("persist", persist)
    g.add_node("sync_external", sync_external)
    g.add_node("ack", ack)

    g.set_entry_point("analyze")
    g.add_edge("analyze", "validate")
    g.add_edge("validate", "check_duplicate")
    g.add_edge("check_duplicate", "persist")
    g.add_edge("persist", "sync_external")
    g.add_edge("sync_external", "ack")
    g.add_edge("ack", END)
    return g.compile()
