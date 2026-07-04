"""
Central configuration for the AI Customer Support Ticket Automation project.

All settings are loaded from environment variables (via a .env file) so that
no secrets are hard-coded in source. See .env.example for the full list of
variables and README.md for setup instructions.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    # --- LLM (Google Gemini) ---
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
    llm_retry_backoff_seconds: float = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "2"))

    # --- Storage paths ---
    sqlite_path: str = os.getenv("SQLITE_PATH", str(BASE_DIR / "data" / "tickets.db"))
    attachments_dir: str = os.getenv("ATTACHMENTS_DIR", str(BASE_DIR / "data" / "attachments"))
    log_dir: str = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

    # --- Outbound acknowledgement email (SMTP) ---
    auto_send_ack: bool = _bool(os.getenv("AUTO_SEND_ACK"), True)
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "")
    smtp_use_tls: bool = _bool(os.getenv("SMTP_USE_TLS"), True)

    # --- Inbound inbox monitoring (IMAP) ---
    imap_host: str = os.getenv("IMAP_HOST", "")
    imap_port: int = int(os.getenv("IMAP_PORT", "993"))
    imap_username: str = os.getenv("IMAP_USERNAME", "")
    imap_password: str = os.getenv("IMAP_PASSWORD", "")
    imap_folder: str = os.getenv("IMAP_FOLDER", "INBOX")
    imap_mark_seen: bool = _bool(os.getenv("IMAP_MARK_SEEN"), True)
    imap_poll_seconds: int = int(os.getenv("IMAP_POLL_SECONDS", "60"))

    # --- Ticket platform integration (Airtable) ---
    airtable_api_key: str = os.getenv("AIRTABLE_API_KEY", "")
    airtable_base_id: str = os.getenv("AIRTABLE_BASE_ID", "")
    airtable_table_name: str = os.getenv("AIRTABLE_TABLE_NAME", "Tickets")

    # --- Duplicate detection ---
    duplicate_lookback_days: int = int(os.getenv("DUPLICATE_LOOKBACK_DAYS", "7"))
    duplicate_similarity_threshold: int = int(os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "85"))

    # --- Team mapping (configurable without touching code) ---
    team_mapping_path: str = os.getenv("TEAM_MAPPING_PATH", str(BASE_DIR / "team_mapping.json"))

    @property
    def imap_enabled(self) -> bool:
        return bool(self.imap_host and self.imap_username and self.imap_password)

    @property
    def smtp_enabled(self) -> bool:
        return all([self.smtp_host, self.smtp_port, self.smtp_username, self.smtp_password, self.smtp_from])

    @property
    def airtable_enabled(self) -> bool:
        return bool(self.airtable_api_key and self.airtable_base_id and self.airtable_table_name)

    def load_team_mapping(self) -> dict:
        """Loads category -> team routing rules from an external JSON file so
        that support-ops can change routing without editing code."""
        default = {
            "Technical Support": "Technical Support",
            "Bug Report": "Technical Support",
            "Billing": "Finance",
            "Refund Request": "Finance",
            "Sales Inquiry": "Sales",
            "Feature Request": "Product Team",
            "Account Access": "Customer Success",
            "General Inquiry": "Customer Success",
        }
        path = Path(self.team_mapping_path)
        if path.exists():
            try:
                return {**default, **json.loads(path.read_text())}
            except Exception:
                return default
        return default


settings = Settings()

Path(settings.attachments_dir).mkdir(parents=True, exist_ok=True)
Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
