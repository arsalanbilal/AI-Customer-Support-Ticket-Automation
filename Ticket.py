import json
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from config import settings
from email_reader import fetch_unseen_emails
from logging_config import get_logger
from ticket_workflow import (
    CATEGORIES, PRIORITIES, SENTIMENTS, TEAM_MAPPING,
    EmailInput, TicketState, build_graph, init_db, list_tickets,
    get_audit_log, update_ticket,
)

load_dotenv()
init_db()
logger = get_logger("app")

st.set_page_config(page_title="AI Support Ticket Automation", layout="wide")
st.title("AI Customer Support Ticket Automation")

if not settings.gemini_api_key:
    st.warning(
        "GEMINI_API_KEY is not set. Set it in your .env file before processing real emails. "
        "You can still explore the UI, but AI analysis will fail and fall back to defaults."
    )

llm = ChatGoogleGenerativeAI(
    model=settings.gemini_model,
    api_key=settings.gemini_api_key,
    temperature=0,
)
graph = build_graph(llm)


def process_email_dict(email_dict: dict):
    email_in = EmailInput(**email_dict)
    result = graph.invoke(TicketState(raw_email=email_in.model_dump()).model_dump())
    return result


# --------------------------------------------------------------------------- #
# Sidebar: ingestion controls
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("1. Ingest Emails")

    st.caption(f"IMAP inbox monitoring: {'✅ configured' if settings.imap_enabled else '⚠️ not configured (see .env.example)'}")
    if st.button("📥 Fetch new emails from inbox (IMAP)"):
        with st.spinner("Connecting to mailbox..."):
            new_emails = fetch_unseen_emails()
        if not new_emails:
            st.info("No new emails found (or IMAP is not configured).")
        else:
            for e in new_emails:
                res = process_email_dict(e)
                st.success(f"Created {res['ticket']['ticket_id']} from inbox email: {e['subject']}")
            st.rerun()

    st.divider()
    st.caption("Load bundled sample emails for a quick demo")
    if st.button("🧪 Load sample_emails.json"):
        samples = json.loads(Path("sample_emails.json").read_text())
        for e in samples:
            res = process_email_dict(e)
            st.success(f"Created {res['ticket']['ticket_id']}: {e['subject']}")
        st.rerun()

    st.divider()
    st.subheader("Manual entry")
    with st.form("manual_email_form", clear_on_submit=True):
        sender_name = st.text_input("Sender Name")
        sender_email = st.text_input("Sender Email")
        subject = st.text_input("Subject")
        body = st.text_area("Body", height=180)
        uploaded = st.file_uploader("Attachments", accept_multiple_files=True)
        submitted = st.form_submit_button("Process Email")

    if submitted:
        attachments = []
        for f in uploaded or []:
            p = Path(settings.attachments_dir) / f.name
            p.write_bytes(f.getbuffer())
            attachments.append(str(p))

        result = process_email_dict({
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject": subject,
            "body": body,
            "attachments": attachments,
        })
        st.success(f"Created {result['ticket']['ticket_id']}")
        if result.get("errors"):
            st.warning("Completed with warnings: " + "; ".join(result["errors"]))
        st.code(result["ack_email"])

    st.divider()
    if st.button("✉️ Send test SMTP email"):
        from email_sender import EmailSender
        mailer = EmailSender()
        result = mailer.send_ack(
            to_email=sender_email or "test@example.com",
            ticket_id="TKT-TEST",
            issue_summary="Test email from app",
            customer_name="Test User",
            assigned_team="Support",
            status="Open",
        )
        st.success("SMTP test email sent") if result["ok"] else st.error(result["message"])

    st.divider()
    st.caption(f"Airtable sync: {'✅ enabled' if settings.airtable_enabled else '⚠️ disabled (tickets stay in local SQLite only)'}")


# --------------------------------------------------------------------------- #
# Main: ticket list + agent review
# --------------------------------------------------------------------------- #
st.subheader("Tickets")

tickets = list_tickets(limit=100)
if not tickets:
    st.info("No tickets yet. Use the sidebar to ingest an email.")

status_options = ["Open", "In Progress", "Waiting for Customer", "Resolved", "Closed"]
team_options = sorted(set(TEAM_MAPPING.values()))

for t in tickets:
    dup_flag = f" | ⚠️ possible duplicate of {t['duplicate_of']}" if t.get("duplicate_of") else ""
    header = f"{t['ticket_id']} | {t['category']} | {t['priority']} | {t['status']}{dup_flag}"
    with st.expander(header):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"**From:** {t['customer_name']} <{t['sender_email']}> ({t.get('company') or 'n/a'})")
            st.markdown(f"**Subject:** {t['email_subject']}")
            st.markdown(f"**Summary:** {t['issue_summary']}")
            st.markdown(f"**Description:** {t['detailed_description']}")
            st.markdown(f"**Sentiment:** {t['sentiment']} | **Confidence:** {t['confidence_score']}")
            st.markdown(f"**Priority reason:** {t.get('priority_reason', '')}")
            tags = json.loads(t.get("suggested_tags") or "[]")
            if tags:
                st.markdown("**Tags:** " + ", ".join(tags))
            attachments = json.loads(t.get("attachments") or "[]")
            if attachments:
                st.markdown("**Attachments:** " + ", ".join(Path(a).name for a in attachments))
            if t.get("airtable_record_id"):
                st.caption(f"Synced to Airtable (record {t['airtable_record_id']})")

            with st.popover("View audit trail"):
                for entry in get_audit_log(t["ticket_id"]):
                    st.caption(f"{entry['timestamp']} — {entry['action']} by {entry['actor']}")

        with col2:
            st.markdown("**Agent review**")
            new_category = st.selectbox("Category", CATEGORIES, index=CATEGORIES.index(t["category"]) if t["category"] in CATEGORIES else 0, key=t["ticket_id"] + "_cat")
            new_priority = st.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(t["priority"]) if t["priority"] in PRIORITIES else 2, key=t["ticket_id"] + "_pri")
            new_team = st.selectbox("Assigned Team", team_options, index=team_options.index(t["assigned_team"]) if t["assigned_team"] in team_options else 0, key=t["ticket_id"] + "_team")
            new_status = st.selectbox("Status", status_options, index=status_options.index(t["status"]) if t["status"] in status_options else 0, key=t["ticket_id"] + "_status")
            note = st.text_area("Internal Notes", value=t.get("internal_notes") or "", key=t["ticket_id"] + "_note")

            if st.button("Save Updates", key=t["ticket_id"] + "_save"):
                update_ticket(t["ticket_id"], {
                    "category": new_category,
                    "priority": new_priority,
                    "assigned_team": new_team,
                    "status": new_status,
                    "internal_notes": note,
                }, actor="agent")
                st.success("Updated")
                st.rerun()
