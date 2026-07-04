"""
All prompts sent to the LLM live here so they can be reviewed, versioned and
submitted as a standalone deliverable independent of the application code.
"""

SYSTEM_PROMPT = """You are an AI customer support triage assistant for a SaaS company.
Read the incoming customer email and extract structured ticket data.
Return ONLY valid, minified JSON. Do not wrap it in markdown code fences.
Do not include any commentary before or after the JSON.
Be conservative when information is not explicitly present - use null rather than guessing."""

ANALYSIS_PROMPT_TEMPLATE = """Analyze the following customer support email and return a single JSON object
with exactly these keys:

- customer_name (string or null): the customer's full name
- company (string or null): the customer's company/organization, if mentioned
- issue_summary (string): a one-line summary of the issue (max ~12 words)
- detailed_description (string): a 2-4 sentence neutral restatement of the issue in your own words
- category (string): one of ["Technical Support", "Billing", "Sales Inquiry", "Feature Request",
  "Bug Report", "Account Access", "Refund Request", "General Inquiry"]
- priority (string): one of ["Critical", "High", "Medium", "Low"]
- priority_reason (string): one short sentence justifying the priority
- sentiment (string): one of ["Positive", "Neutral", "Negative"]
- product_service (string or null): the product/service/feature the customer refers to
- suggested_department (string): the team best suited to handle this
  (Technical Support, Finance, Sales, Customer Success, or Product Team)
- suggested_tags (array of strings): 2-5 short lowercase keyword tags, no duplicates
- confidence_score (number): your confidence in this classification, between 0 and 1

Rules:
- Output must be valid JSON and nothing else (no markdown fences, no prose).
- If a field cannot be determined, use null (or an empty array for suggested_tags).
- Priority guidance: "Critical" = production down / data loss / security incident;
  "High" = blocking a paying customer from core functionality or billing dispute;
  "Medium" = degraded but workable; "Low" = general questions, feature requests, minor issues.

Email metadata:
Sender Name: {sender_name}
Sender Email: {sender_email}
Received At: {received_at}
Subject: {subject}

Email Body:
\"\"\"
{body}
\"\"\"
"""

ACK_EMAIL_TEMPLATE = """Hello {customer_name},

Thanks for reaching out. We've received your request and created a support ticket for you.

Ticket ID: {ticket_id}
Summary: {issue_summary}
Current Status: {status}
Estimated Response Time: {eta}

Our {assigned_team} team will follow up as soon as possible. You can reply to this email
if you'd like to add more details.

Regards,
Support Team
"""
