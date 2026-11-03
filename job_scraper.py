"""
job_scraper.py
--------------

AI-assisted job discovery pipeline for configurable job searches.

The pipeline:
- Collects listings from configurable job sources.
- Applies rule-based seniority filtering.
- Uses Claude to evaluate language and experience requirements.
- Deduplicates listings across runs using state stored in a GitHub Gist.
- Supports optional Adzuna collection and Gmail delivery.

Configuration is provided through environment variables.
See .env.example for the complete configuration template.
"""

from __future__ import annotations

import json
import os
import random
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import pandas as pd
import requests
from jobspy import scrape_jobs


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def required_env(name: str) -> str:
    """
    Return a required environment variable.

    Raises a descriptive error when the variable is missing or empty.
    """
    value = os.environ.get(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not configured."
        )

    return value


def optional_env(name: str, default: str = "") -> str:
    """Return an optional environment variable."""
    return os.environ.get(name, default).strip()


def int_env(name: str, default: int) -> int:
    """Read an integer environment variable."""
    value = optional_env(name)

    if not value:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable '{name}' must be an integer."
        ) from exc


def float_env(name: str, default: float) -> float:
    """Read a floating-point environment variable."""
    value = optional_env(name)

    if not value:
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable '{name}' must be a number."
        ) from exc


def csv_env(
    name: str,
    default: str = "",
    *,
    lowercase: bool = False,
) -> list[str]:
    """
    Read a comma-separated environment variable as a cleaned list.
    """
    raw_value = optional_env(name, default)

    values = [
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    ]

    if lowercase:
        values = [item.lower() for item in values]

    return values


# ---------------------------------------------------------------------------
# Required credentials
# ---------------------------------------------------------------------------

CLAUDE_API_KEY = required_env("CLAUDE_API_KEY")
GIST_TOKEN = required_env("GIST_TOKEN")
GIST_ID = required_env("GIST_ID")


# ---------------------------------------------------------------------------
# Job-search configuration
# ---------------------------------------------------------------------------

SEARCH_TERMS = csv_env(
    "SEARCH_TERMS",
    "data analyst,business analyst",
)

JOB_SOURCES = csv_env(
    "JOB_SOURCES",
    "indeed",
    lowercase=True,
)

LOCATION = optional_env("LOCATION", "Belgium")
INDEED_COUNTRY = optional_env("INDEED_COUNTRY", "belgium")

HOURS_OLD = int_env("HOURS_OLD", 24)
RESULTS_EACH = int_env("RESULTS_EACH", 20)

DESCRIPTION_FORMAT = optional_env(
    "DESCRIPTION_FORMAT",
    "markdown",
)

DESCRIPTION_SNIPPET_LEN = int_env(
    "DESCRIPTION_SNIPPET_LEN",
    600,
)

SEEN_URLS_TTL_DAYS = int_env(
    "SEEN_URLS_TTL_DAYS",
    7,
)


# ---------------------------------------------------------------------------
# JobSpy source-specific configuration
# ---------------------------------------------------------------------------

LINKEDIN_FETCH_DESCRIPTION = (
    optional_env("LINKEDIN_FETCH_DESCRIPTION", "false").lower()
    in {"1", "true", "yes", "on"}
)


# ---------------------------------------------------------------------------
# Adzuna configuration
# ---------------------------------------------------------------------------

ADZUNA_APP_ID = optional_env("ADZUNA_APP_ID")
ADZUNA_API_KEY = optional_env("ADZUNA_API_KEY")

ADZUNA_ENABLED = bool(
    ADZUNA_APP_ID and ADZUNA_API_KEY
)

ADZUNA_COUNTRY = optional_env(
    "ADZUNA_COUNTRY",
    "be",
)

ADZUNA_PAGE = int_env(
    "ADZUNA_PAGE",
    1,
)

ADZUNA_MAX_DAYS_OLD = int_env(
    "ADZUNA_MAX_DAYS_OLD",
    7,
)

ADZUNA_BASE_URL = optional_env(
    "ADZUNA_BASE_URL",
    "https://api.adzuna.com/v1/api/jobs",
)


# ---------------------------------------------------------------------------
# Filtering configuration
# ---------------------------------------------------------------------------

SENIOR_TITLE_KEYWORDS = csv_env(
    "SENIOR_TITLE_KEYWORDS",
    (
        "senior,sr.,principal,head of,director,vice president,"
        "vp,chief,c-level,staff engineer,distinguished,architect"
    ),
    lowercase=True,
)

SENIOR_JOB_LEVELS = set(
    csv_env(
        "SENIOR_JOB_LEVELS",
        "mid-senior level,senior,director,executive,management",
        lowercase=True,
    )
)

MAX_JUNIOR_SALARY = float_env(
    "MAX_JUNIOR_SALARY",
    55000,
)


# ---------------------------------------------------------------------------
# Claude configuration
# ---------------------------------------------------------------------------

CLAUDE_MODEL = optional_env(
    "CLAUDE_MODEL",
    "claude-haiku-4-5-20251001",
)

CLAUDE_MAX_TOKENS = int_env(
    "CLAUDE_MAX_TOKENS",
    5,
)

CLAUDE_DESCRIPTION_LIMIT = int_env(
    "CLAUDE_DESCRIPTION_LIMIT",
    2500,
)

LLM_BATCH_SIZE = int_env(
    "LLM_BATCH_SIZE",
    10,
)

LLM_BATCH_SLEEP_SECONDS = float_env(
    "LLM_BATCH_SLEEP_SECONDS",
    1,
)


# ---------------------------------------------------------------------------
# Request and timing configuration
# ---------------------------------------------------------------------------

HTTP_TIMEOUT_SECONDS = int_env(
    "HTTP_TIMEOUT_SECONDS",
    15,
)

GIST_UPDATE_TIMEOUT_SECONDS = int_env(
    "GIST_UPDATE_TIMEOUT_SECONDS",
    30,
)

SOURCE_SLEEP_MIN_SECONDS = float_env(
    "SOURCE_SLEEP_MIN_SECONDS",
    2,
)

SOURCE_SLEEP_MAX_SECONDS = float_env(
    "SOURCE_SLEEP_MAX_SECONDS",
    5,
)

ADZUNA_SLEEP_MIN_SECONDS = float_env(
    "ADZUNA_SLEEP_MIN_SECONDS",
    1,
)

ADZUNA_SLEEP_MAX_SECONDS = float_env(
    "ADZUNA_SLEEP_MAX_SECONDS",
    2,
)


# ---------------------------------------------------------------------------
# Gmail configuration
# ---------------------------------------------------------------------------

GMAIL_FROM = optional_env("GMAIL_FROM")
GMAIL_APP_PASSWORD = optional_env("GMAIL_APP_PASSWORD")
GMAIL_TO = optional_env("GMAIL_TO") or GMAIL_FROM

SMTP_HOST = optional_env(
    "SMTP_HOST",
    "smtp.gmail.com",
)

SMTP_PORT = int_env(
    "SMTP_PORT",
    465,
)

EMAIL_SUBJECT_PREFIX = optional_env(
    "EMAIL_SUBJECT_PREFIX",
    "Job Board",
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_configuration() -> None:
    """Validate configuration before starting the pipeline."""

    if not SEARCH_TERMS:
        raise RuntimeError(
            "SEARCH_TERMS must contain at least one search term."
        )

    if not JOB_SOURCES and not ADZUNA_ENABLED:
        raise RuntimeError(
            "Configure at least one JOB_SOURCES value or enable Adzuna."
        )

    if SOURCE_SLEEP_MIN_SECONDS > SOURCE_SLEEP_MAX_SECONDS:
        raise RuntimeError(
            "SOURCE_SLEEP_MIN_SECONDS cannot be greater than "
            "SOURCE_SLEEP_MAX_SECONDS."
        )

    if ADZUNA_SLEEP_MIN_SECONDS > ADZUNA_SLEEP_MAX_SECONDS:
        raise RuntimeError(
            "ADZUNA_SLEEP_MIN_SECONDS cannot be greater than "
            "ADZUNA_SLEEP_MAX_SECONDS."
        )


# ---------------------------------------------------------------------------
# GitHub Gist state
# ---------------------------------------------------------------------------

def gist_headers() -> dict[str, str]:
    """Return headers used for GitHub Gist API requests."""
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def fetch_current_gist() -> dict:
    """Retrieve the current jobs and seen-URL state from GitHub Gist."""

    try:
        response = requests.get(
            f"https
