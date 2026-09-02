"""
Path utilities for consistent file path handling across all teams.

This module provides absolute paths to avoid issues with relative paths
when the working directory changes (e.g., when running from web UI backend).
"""

from pathlib import Path

# Absolute path to project root
# Since path_utils.py is now in utils/, go up one level to get project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Output directories (grouped under outputs/)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PAPERS_DIR = OUTPUTS_DIR / "papers"
ANALYSES_DIR = OUTPUTS_DIR / "analyses"
DATASETS_DIR = OUTPUTS_DIR / "datasets"
DATABASE_DIR = OUTPUTS_DIR / "database"

# Agent notes / MCP filesystem root (under outputs/, gitignored with other pipeline output)
AGENT_NOTES_DIR = OUTPUTS_DIR / "notes"
WEBSITE_NOTES_DIR = AGENT_NOTES_DIR / "websites"
RESEARCH_DOCS_DIR = AGENT_NOTES_DIR / "research_docs"

# Backward compatibility aliases (deprecated - use new names)
NOTES_DIR = AGENT_NOTES_DIR
PROJECT_NOTES_DIR = ANALYSES_DIR
BROWSERUSE_DATASETS_DIR = DATASETS_DIR

def ensure_directories_exist():
    """Ensure all required directories exist."""
    OUTPUTS_DIR.mkdir(exist_ok=True)
    PAPERS_DIR.mkdir(exist_ok=True)
    ANALYSES_DIR.mkdir(exist_ok=True)
    DATASETS_DIR.mkdir(exist_ok=True)
    AGENT_NOTES_DIR.mkdir(exist_ok=True)
    WEBSITE_NOTES_DIR.mkdir(exist_ok=True)
    RESEARCH_DOCS_DIR.mkdir(exist_ok=True)

def get_outputs_dir() -> str:
    """Get the outputs directory path as string."""
    return str(OUTPUTS_DIR)

def get_papers_dir() -> str:
    """Get the papers directory path as string."""
    return str(PAPERS_DIR)

def get_analyses_dir() -> str:
    """Get the analyses directory path as string."""
    return str(ANALYSES_DIR)

def get_datasets_dir() -> str:
    """Get the datasets directory path as string."""
    return str(DATASETS_DIR)

def get_database_dir() -> str:
    """Get the database directory path as string."""
    return str(DATABASE_DIR)

def get_notes_dir() -> str:
    """Get the agent notes directory path as string (`outputs/notes`)."""
    return str(AGENT_NOTES_DIR)

def get_website_notes_dir() -> str:
    """Get the website notes directory path as string."""
    return str(WEBSITE_NOTES_DIR)

def get_project_notes_dir() -> str:
    """Get the project notes (analyses) directory path as string (alias for backward compatibility)."""
    return str(ANALYSES_DIR)

def get_browseruse_datasets_dir() -> str:
    """Get the datasets directory path as string (alias for backward compatibility)."""
    return str(DATASETS_DIR)

def get_shared_knowledge_dir() -> str:
    """Backward-compatible alias for `get_notes_dir()` (`outputs/notes`)."""
    return str(AGENT_NOTES_DIR)

def get_research_docs_dir() -> str:
    """Get the research docs directory path as string."""
    return str(RESEARCH_DOCS_DIR)
