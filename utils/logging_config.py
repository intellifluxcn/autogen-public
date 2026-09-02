"""Centralized logging configuration for app entrypoints."""

import logging
import logging.config
import os
from typing import Any, Dict


def _build_logging_config(level_name: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
            "uvicorn_access": {
                "format": "%(asctime)s - uvicorn.access - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "uvicorn_access",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {
            "level": level_name,
            "handlers": ["default"],
        },
        "loggers": {
            "uvicorn.error": {
                "level": level_name,
                "handlers": ["default"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": level_name,
                "handlers": ["access"],
                "propagate": False,
            },
        },
    }


def configure_logging(default_level: str = "INFO") -> None:
    """Configure root/app/uvicorn loggers from one place."""
    level_name = os.getenv("LOG_LEVEL", default_level).upper()
    if level_name not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        level_name = default_level.upper()

    logging.config.dictConfig(_build_logging_config(level_name))
