"""Utility for parsing author information from analysis markdown files."""

import re
from typing import List


class AuthorParser:
    """Parse author names and email addresses from analysis markdown content."""

    @staticmethod
    def _parse_labeled_field(content: str, label: str) -> str:
        if not content:
            return ""
        pattern = rf"(?im)^\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*:\*{{0,2}}\s*(.+?)\s*$"
        match = re.search(pattern, content)
        return match.group(1).strip().strip("*").strip() if match else ""

    @staticmethod
    def parse_title(content: str) -> str:
        """Parse paper title from markdown metadata lines."""
        return AuthorParser._parse_labeled_field(content, "Title")

    @staticmethod
    def parse_authors(content: str) -> List[str]:
        """Parse author names from markdown metadata lines."""
        author_line = AuthorParser._parse_labeled_field(content, "Authors")
        if not author_line:
            return []
        return [name.strip() for name in author_line.split(',') if name.strip()]

    @staticmethod
    def parse_emails(content: str) -> List[str]:
        """Extract unique email addresses from content."""
        if not content:
            return []
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, content)
        seen = set()
        unique_emails = []
        for email in emails:
            if email.lower() not in seen:
                seen.add(email.lower())
                unique_emails.append(email)
        return unique_emails

    @staticmethod
    def parse_author_info(content: str) -> dict:
        """Parse title, authors, and emails from content."""
        return {
            'title': AuthorParser.parse_title(content),
            'authors': AuthorParser.parse_authors(content),
            'emails': AuthorParser.parse_emails(content)
        }
