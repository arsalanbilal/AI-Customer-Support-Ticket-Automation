-- Tickets: system-of-record for the automation pipeline.
-- Mirrored into Airtable (see airtable_client.py) when Airtable credentials
-- are configured; airtable_record_id links the two records together.
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id            TEXT PRIMARY KEY,
  customer_name        TEXT,
  company              TEXT,
  sender_email         TEXT,
  sender_name          TEXT,
  email_subject        TEXT,
  email_body           TEXT,
  issue_summary        TEXT,
  detailed_description TEXT,
  category             TEXT,
  priority             TEXT,
  priority_reason      TEXT,
  sentiment            TEXT,
  product_service      TEXT,
  suggested_department TEXT,
  suggested_tags       TEXT,   -- JSON array, stored as text
  confidence_score     REAL,
  assigned_team        TEXT,
  status               TEXT,
  internal_notes       TEXT,
  attachments          TEXT,   -- JSON array of file paths, stored as text
  duplicate_of         TEXT,   -- ticket_id of an earlier, similar ticket (if any)
  airtable_record_id   TEXT,   -- Airtable record id, if Airtable sync is enabled
  original_email_json  TEXT,
  received_at          TEXT,
  last_updated          TEXT
);

-- Full audit trail of every create/update action performed on a ticket,
-- either by the system or by a support agent.
CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id   TEXT,
  action      TEXT,       -- e.g. "create", "update"
  old_value   TEXT,        -- JSON snapshot before the change
  new_value   TEXT,        -- JSON of the fields that changed
  actor       TEXT,        -- "system" or an agent identifier
  timestamp   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_sender_email ON tickets(sender_email);
CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_audit_ticket_id ON audit_log(ticket_id);
