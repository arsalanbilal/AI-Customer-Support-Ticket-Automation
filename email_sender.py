"""
Sends the customer acknowledgement email over SMTP.

If SMTP credentials are not configured (common in a local/demo environment),
send_ack() returns {"ok": False, "message": "SMTP not configured"} instead of
raising, so the rest of the workflow (ticket creation, routing) still
completes successfully.
"""
import smtplib
from email.mime.text import MIMEText

from config import settings
from logging_config import get_logger
from prompts import ACK_EMAIL_TEMPLATE

logger = get_logger("email_sender")


class EmailSender:
    def __init__(self):
        self.enabled = settings.smtp_enabled

    def render_ack_body(self, ticket_id: str, customer_name: str, issue_summary: str,
                         status: str, assigned_team: str, eta: str = "24-48 hours") -> str:
        return ACK_EMAIL_TEMPLATE.format(
            customer_name=customer_name or "there",
            ticket_id=ticket_id,
            issue_summary=issue_summary,
            status=status,
            assigned_team=assigned_team or "Support",
            eta=eta,
        )

    def send_ack(self, to_email: str, ticket_id: str, issue_summary: str,
                 customer_name: str = "", assigned_team: str = "Support",
                 status: str = "Open", eta: str = "24-48 hours") -> dict:
        if not self.enabled:
            logger.info("SMTP not configured - skipping acknowledgement email for %s", ticket_id)
            return {"ok": False, "message": "SMTP not configured"}

        body = self.render_ack_body(ticket_id, customer_name, issue_summary, status, assigned_team, eta)
        msg = MIMEText(body)
        msg["Subject"] = f"Support Ticket Received: {ticket_id}"
        msg["From"] = settings.smtp_from
        msg["To"] = to_email

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                if settings.smtp_use_tls:
                    server.starttls()
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
            logger.info("Acknowledgement email sent for %s to %s", ticket_id, to_email)
            return {"ok": True, "message": "Email sent"}
        except Exception as e:
            logger.error("Failed to send acknowledgement email for %s: %s", ticket_id, e)
            return {"ok": False, "message": str(e)}
