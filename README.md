# AI Customer Support Ticket Automation

An AI-powered pipeline that ingests customer support emails, classifies and
prioritizes them with an LLM, creates tickets (locally + optionally in
Airtable), routes them to the right team, sends an acknowledgement email, and
gives support agents a review UI with a full audit trail.

Built in Python (Streamlit + LangGraph + Google Gemini) instead of n8n — see
"Assumptions" below for why, and `workflow_export.json` for an n8n-style
JSON export of the pipeline graph.

## What changed from the first draft

This version fixes a fatal bug and closes the gaps identified in review:

- **Fixed a crash**: `ticket_workflow.py` had a bad indentation in the `ack`
  node that raised `IndentationError` on import — the app could not run at
  all. Fixed.
- **AI prompts are now actually used.** Previously `prompts.py` was written
  but never sent to the LLM; the model received the raw email with no
  instructions. Now `analyze()` sends `SYSTEM_PROMPT` + `ANALYSIS_PROMPT_TEMPLATE`.
- **Real inbox monitoring.** Added `email_reader.py` (IMAP) to detect and
  parse real incoming emails, including attachments — not just a manual form.
- **Ticket platform integration.** Added `airtable_client.py` so tickets are
  created/updated in Airtable (Step 6 of the spec), in addition to the local
  SQLite audit store.
- **Configurable routing.** Category → team mapping now lives in
  `team_mapping.json`, not hard-coded in Python.
- **Fuller agent review.** The UI now lets agents edit category, priority,
  assigned team, status, and internal notes (previously only status + notes),
  and view the full audit trail per ticket.
- **Duplicate detection**, **LLM retry with backoff**, and **file + console
  logging** were added (`logging_config.py`).
- Expanded `sample_emails.json` to cover every category, all priority levels,
  and a duplicate-ticket scenario.
- Added unit tests (`tests/test_ticket_workflow.py`).

## Architecture

```
IMAP inbox  ──┐
Manual form ──┼──> analyze (LLM) ──> validate ──> check_duplicate ──> persist (SQLite)
Sample data ──┘                                                          │
                                                                          v
                                                                  sync_external (Airtable)
                                                                          │
                                                                          v
                                                                   ack (SMTP email)
```

Agents then use the Streamlit UI to review, edit, and progress tickets through
`Open → In Progress → Waiting for Customer → Resolved → Closed`. Every change
is recorded in `audit_log`.

## Project structure

```
app.py                 Streamlit UI (ingestion + agent review)
config.py              All settings, loaded from environment variables
ticket_workflow.py     Core LangGraph pipeline (analyze/validate/dedupe/persist/sync/ack)
email_reader.py        IMAP inbox monitoring (Step 1)
email_sender.py        SMTP acknowledgement emails (Step 8)
airtable_client.py     Airtable ticket creation/update (Step 6)
prompts.py             All prompts sent to the LLM (deliverable #3)
schema.sql             SQLite schema: tickets + audit_log (deliverable #2)
team_mapping.json      Configurable category -> team routing (Step 7)
sample_emails.json     Sample test emails covering all categories (deliverable #4)
workflow_export.json   JSON export of the pipeline graph (deliverable #1)
logging_config.py      Rotating file + console logging
tests/                 Unit tests for the deterministic logic
data/                  SQLite DB + saved attachments (created at runtime)
logs/                  app.log (created at runtime)
.env.example           All environment variables, documented
```

## Setup

1. **Python 3.10+** recommended.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the environment template and fill in real values:
   ```bash
   cp .env.example .env
   ```
4. Run the app:
   ```bash
   streamlit run app.py
   ```
5. In the sidebar, either:
   - click **"Load sample_emails.json"** for an instant demo with no external
     accounts needed, or
   - click **"Fetch new emails from inbox (IMAP)"** if you configured IMAP, or
   - fill in the manual entry form.

## Required / optional environment variables

Only `GEMINI_API_KEY` is strictly required for AI classification to work.
Everything else degrades gracefully if left blank (see `.env.example` for the
full list and defaults):

| Variable | Required? | Effect if missing |
|---|---|---|
| `GEMINI_API_KEY` | Yes | AI analysis fails per-email; ticket still gets created with default/fallback values and a note flagging it for manual review. |
| `SMTP_*` | No | Acknowledgement emails are skipped (logged, not sent). |
| `IMAP_*` | No | "Fetch new emails" returns nothing; use manual entry / sample data instead. |
| `AIRTABLE_*` | No | Tickets stay in local SQLite only; no Airtable sync. |
| `TEAM_MAPPING_PATH` | No | Falls back to the built-in defaults in `config.py`. |

## Running tests

```bash
pytest tests/ -v
```

Covers category/priority/sentiment normalization, tag deduplication, email
validation, business-rule priority overrides, and tolerant JSON parsing of
AI output. LLM/SMTP/IMAP calls are intentionally excluded from unit tests
since they require live credentials — exercise those via the screen
recording / manual demo instead.

## Assumptions made

- **Implementation approach**: the spec allows "n8n, OpenAI/Claude/Gemini,
  Airtable/Notion/NocoDB, JavaScript, ... you are free to choose your
  implementation approach." We chose a Python/LangGraph/Streamlit stack
  instead of n8n because it's easier to unit test and version-control end to
  end; `workflow_export.json` provides an n8n-style JSON description of the
  same pipeline for review purposes.
- **LLM provider**: Google Gemini (`gemini-1.5-flash`) was used since that's
  what the original credentials/config targeted; swapping to OpenAI or Claude
  only requires changing the LLM client construction in `app.py` — the rest
  of the pipeline (prompts, validation, routing) is provider-agnostic.
- **Ticket platform**: Airtable was chosen among Airtable/Notion/NocoDB
  because its REST API needs only an API key + base/table name, no extra
  infrastructure. SQLite remains the system of record for the audit trail
  and for offline/demo use when Airtable isn't configured.
- **Duplicate detection**: two tickets are considered possible duplicates if
  they're from the same sender, in the same category, within
  `DUPLICATE_LOOKBACK_DAYS` days, and their issue summaries have a fuzzy
  similarity above `DUPLICATE_SIMILARITY_THRESHOLD`. Duplicates are flagged
  (not auto-merged or auto-closed) so an agent makes the final call.
- **Priority logic**: a small set of deterministic keyword rules (outage,
  data loss, "cannot log in" + urgent language, refund disputes) can upgrade
  the AI's suggested priority, but never downgrade it below what the rules
  require — this is meant as a safety net against the LLM under-prioritizing
  clearly urgent language.
- **Attachments** from real inbound emails are saved to `ATTACHMENTS_DIR`
  with a filename prefixed by the sender's email handle to avoid collisions;
  manual-entry attachments uploaded via Streamlit are saved as-is.

## Known limitations / not implemented

- IMAP polling is on-demand (triggered by the "Fetch" button in the UI), not
  a background daemon. For continuous production polling, wrap
  `email_reader.fetch_unseen_emails()` in a scheduler (cron / systemd timer /
  `while True: sleep(IMAP_POLL_SECONDS)` loop) and call
  `ticket_workflow.build_graph(...).invoke(...)` for each result.
- Notion/NocoDB integrations were not built (Airtable was chosen instead per
  "Assumptions" above); the `airtable_client.py` pattern (REST call + field
  mapping) can be adapted to either if needed.
- No authentication/authorization layer on the Streamlit UI — anyone with
  the URL can view/edit tickets. Add an auth proxy or Streamlit's
  built-in auth for a real deployment.
