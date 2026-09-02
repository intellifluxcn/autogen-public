"""Reusable helpers for contact-author email generation and sending."""

import logging
import smtplib
from typing import List, Optional

from database.author_parser import AuthorParser
from database.dao import ProjectDAO
from utils.llm_config import build_openrouter_sync_client, get_openrouter_model
from utils.pipeline_log import pipeline_log
from web_ui.backend.smtp_client import SMTPNotConfigured, send_email

logger = logging.getLogger(__name__)


def generate_contact_email(*, dao: ProjectDAO, project_id: str, artifact_id: int) -> dict:
    """Generate a concise contact-author email for an analysis artifact."""

    file_content = dao.get_artifact_content(artifact_id)
    if not file_content:
        raise ValueError("Artifact not found")

    project = dao.get_project(project_id)
    research_query = project.get("research_query", "") if project else ""

    author_info = AuthorParser.parse_author_info(file_content)
    paper_title = author_info.get("title", "")
    authors = author_info.get("authors", [])
    first_author_last = authors[0].strip().split()[-1] if authors else ""

    prompt = f"""Write a professional email requesting access to research data. Keep it concise (under 150 words).

Paper: "{paper_title}"
Authors: {', '.join(authors) if authors else 'Unknown'}
First author last name (for greeting): {first_author_last or 'the author'}

Paper analysis excerpt:
{file_content[:2000]}

Our research context: {research_query}

Requirements:
- Greet with "Hello Professor {first_author_last}" (or "Hello Professor" if no name)
- Reference the specific paper by title
- Mention what specific data from their paper interests us and why (based on the analysis)
- Briefly explain our research goal and why their data would be valuable
- Ask politely for access or direction to where data can be obtained
- End with "Thank you for your consideration.\\n\\nBest regards"
- Do NOT include a sender name
- Do NOT include a subject line
- Output ONLY the email body text, nothing else"""

    client = build_openrouter_sync_client()
    response = client.chat.completions.create(
        model=get_openrouter_model("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500,
    )
    body = (response.choices[0].message.content or "").strip()
    subject = f"Data Request - {paper_title}" if paper_title else "Data Request"
    return {"subject": subject, "body": body, "author_info": author_info}


def send_contact_email(
    *,
    dao: ProjectDAO,
    recipients: List[str],
    subject: str,
    body: str,
    artifact_id: Optional[int] = None,
) -> dict:
    """Send a contact-author email via the configured SMTP server."""

    try:
        sender_email = send_email(recipients=recipients, subject=subject, body=body)
    except SMTPNotConfigured as e:
        raise RuntimeError(str(e)) from e
    except smtplib.SMTPException as e:
        pipeline_log(
            f"email SMTP error: {e}",
            stage="pipeline",
            component="email",
            level=logging.ERROR,
        )
        logger.exception("Failed to send contact email")
        raise RuntimeError(f"SMTP error: {e}") from e

    pipeline_log(
        f"email sent successfully recipients={recipients}",
        stage="pipeline",
        component="email",
    )

    if artifact_id is not None:
        dao.mark_email_sent(artifact_id)
        pipeline_log(
            f"email metadata updated artifact_id={artifact_id}",
            stage="pipeline",
            component="email",
        )

    return {
        "success": True,
        "message": f"Email sent to {len(recipients)} recipient(s)",
        "sender_email": sender_email,
    }
