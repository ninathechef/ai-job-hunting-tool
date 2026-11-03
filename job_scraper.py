"""
job_scraper.py
--------------
Daily LinkedIn + Google job scraper for junior positions in Belgium.
- Filters Dutch/French language and senior-level roles using Claude Haiku.
- Deduplicates across runs using a seen_urls log stored in the Gist.

Required environment variables:
  CLAUDE_API_KEY   - Anthropic API key (console.anthropic.com)
  GIST_TOKEN       - GitHub personal access token with 'gist' scope
  GIST_ID          - ID of your GitHub Gist (created once during setup)
"""

from __future__ import annotations

import json
import os
import time
import random
from datetime import datetime, timezone, timedelta

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import pandas as pd
import requests
from jobspy import scrape_jobs


# ── Configuration ────────────────────────────────────────────────────────────

CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
GIST_TOKEN     = os.environ["GIST_TOKEN"]
GIST_ID        = os.environ["GIST_ID"]

SEARCH_TERMS = [
    "data analyst",
    "business analyst",
    "AI",
    "automation",
    "business transformation",
    "digital transformation analyst",
    "process analyst",
    "innovation",
    "junior data analyst",
    "junior business analyst",
    "junior analyst",
    "BI analyst",
    "business intelligence",
    "reporting analyst",
    "graduate",
    "young graduate",
    "transformation",
]

SITES                   = ["linkedin", "indeed"]   # indeed uses country_indeed="belgium"

# Adzuna API (optional second source — covers Stepstone.be, Jobat.be, company pages)
# Sign up free at https://developer.adzuna.com → add ADZUNA_APP_ID and ADZUNA_API_KEY to GitHub secrets
# Leave empty strings to disable Adzuna
ADZUNA_APP_ID  = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_API_KEY = os.environ.get("ADZUNA_API_KEY", "")
ADZUNA_ENABLED = bool(ADZUNA_APP_ID and ADZUNA_API_KEY)

# Gmail delivery — Cowork scheduled task reads jobs from this email instead
# of fetching the Gist URL (avoids URL provenance restrictions).
# Create an App Password at: https://myaccount.google.com/apppasswords
GMAIL_FROM         = os.environ.get("GMAIL_FROM", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO           = os.environ.get("GMAIL_TO", GMAIL_FROM)  # defaults to send-to-self
LOCATION                = "Belgium"
HOURS_OLD               = 24    # 24h catches recent posts; seen_urls prevents duplicates
RESULTS_EACH            = 20
DESCRIPTION_SNIPPET_LEN = 600
SEEN_URLS_TTL_DAYS      = 7

# Pre-LLM title keyword exclusions (clear senior signals — free, no API call)
SENIOR_TITLE_KEYWORDS = [
    "senior", " sr.", "sr ", "principal", "head of", "head,",
    "director", "vice president", " vp ", "chief ", "c-level",
    "staff engineer", "distinguished", "architect",
]

# LinkedIn job_level values that are clearly too senior
SENIOR_JOB_LEVELS = {
    "mid-senior level", "senior", "director", "executive", "management",
}


# ── Gist read/write ───────────────────────────────────────────────────────────

def fetch_current_gist() -> dict:
    try:
        response = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={
                "Authorization": f"token {GIST_TOKEN}",
                "Accept":        "application/vnd.github+json",
            },
            timeout=15,
        )
        response.raise_for_status()
        files = response.json().get("files", {})
        raw = files.get("jobs.json", {}).get("content", "{}")
        return json.loads(raw)
    except Exception as e:
        print(f"Warning: could not fetch current Gist state: {e}")
        return {}


def update_gist(jobs: list[dict], seen_urls: dict[str, str]) -> str:
    payload = {
        "files": {
            "jobs.json": {
                "content": json.dumps(
                    {
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                        "job_count":    len(jobs),
                        "jobs":         jobs,
                        "seen_urls":    seen_urls,
                    },
                    indent=2,
                    default=str,
                )
            }
        }
    }
    response = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept":        "application/vnd.github+json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    gist_url = response.json().get("html_url", "")
    print(f"Gist updated: {gist_url}")
    return gist_url


# ── Seen-URLs deduplication ───────────────────────────────────────────────────

def load_seen_urls(gist_data: dict) -> dict[str, str]:
    seen = gist_data.get("seen_urls", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_URLS_TTL_DAYS)).date()
    pruned = {url: d for url, d in seen.items() if d >= str(cutoff)}
    removed = len(seen) - len(pruned)
    if removed:
        print(f"Pruned {removed} expired entries from seen_urls")
    return pruned


def mark_seen(url: str, seen_urls: dict[str, str]) -> None:
    seen_urls[url] = str(datetime.now(timezone.utc).date())


# ── Pre-LLM seniority filter (free) ──────────────────────────────────────────

def is_clearly_senior(title: str, job_level: str) -> bool:
    """
    Fast check using LinkedIn metadata and title keywords.
    Catches obvious senior roles before spending an API call.
    """
    title_lower = (title or "").lower()
    level_lower = (job_level or "").lower().strip()

    # Check job_level metadata from LinkedIn
    if level_lower in SENIOR_JOB_LEVELS:
        return True

    # Check title for senior keywords
    if any(kw in title_lower for kw in SENIOR_TITLE_KEYWORDS):
        return True

    return False


# ── Scraping ──────────────────────────────────────────────────────────────────

def scrape_site(search_term: str, site: str) -> pd.DataFrame:
    try:
        print(f"  → [{site}] '{search_term}'")

        if site == "indeed":
            df = scrape_jobs(
                site_name="indeed",
                search_term=search_term,
                location=LOCATION,
                country_indeed="belgium",
                results_wanted=RESULTS_EACH,
                hours_old=HOURS_OLD,
                description_format="markdown",
                verbose=0,
            )
        else:  # linkedin
            df = scrape_jobs(
                site_name=site,
                search_term=search_term,
                location=LOCATION,
                results_wanted=RESULTS_EACH,
                hours_old=HOURS_OLD,
                linkedin_fetch_description=True,
                description_format="markdown",
                verbose=0,
            )

        print(f"     Found {len(df)} jobs")
        return df
    except Exception as e:
        print(f"     Error: {e}")
        return pd.DataFrame()


def scrape_adzuna(search_term: str) -> list[dict]:
    """
    Calls the Adzuna REST API for Belgian job listings.
    Returns a list of raw job dicts (same schema as collect_all_jobs output).
    Covers Stepstone.be, Jobat.be, and company career pages not on LinkedIn/Indeed.
    """
    if not ADZUNA_ENABLED:
        return []
    try:
        url = "https://api.adzuna.com/v1/api/jobs/be/search/1"
        params = {
            "app_id":           ADZUNA_APP_ID,
            "app_key":          ADZUNA_API_KEY,
            "what":             search_term,
            "results_per_page": RESULTS_EACH,
            "max_days_old":     7,
        }
        headers = {"Accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("results", []):
            jobs.append({
                "title":       j.get("title"),
                "company":     j.get("company", {}).get("display_name"),
                "location":    j.get("location", {}).get("display_name"),
                "date_posted": j.get("created")[:10] if j.get("created") else "",
                "job_url":     j.get("redirect_url"),
                "job_type":    None,
                "job_level":   None,
                "site":        "adzuna",
                "description": j.get("description") or "",
                "salary_max":  j.get("salary_max"),
            })
        print(f"     Found {len(jobs)} jobs")
        return jobs
    except Exception as e:
        print(f"     Adzuna error: {e}")
        return []


def collect_all_jobs(seen_urls: dict[str, str]) -> list[dict]:
    all_jobs: list[dict] = []
    seen_this_run: set[str] = set()
    skipped_previous = 0
    skipped_senior_preflight = 0

    for term in SEARCH_TERMS:
        # Adzuna (if enabled) — runs once per term, not per site
        if ADZUNA_ENABLED:
            print(f"  → [adzuna] '{term}'")
            adzuna_jobs = scrape_adzuna(term)
            for job in adzuna_jobs:
                url = job.get("job_url", "")
                if not url or url in seen_this_run:
                    continue
                seen_this_run.add(url)
                if url in seen_urls:
                    skipped_previous += 1
                    continue
                if is_clearly_senior(job.get("title", ""), job.get("job_level", "")):
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)
                    continue
                # High salary = likely senior (Belgian junior roles rarely exceed €55k)
                if job.get("salary_max") and job["salary_max"] > 55000:
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)
                    continue
                all_jobs.append(job)
            time.sleep(random.uniform(1, 2))

        for site in SITES:
            df = scrape_site(term, site)
            if df.empty:
                continue

            for _, row in df.iterrows():
                url = row.get("job_url", "")
                if not url or url in seen_this_run:
                    continue
                seen_this_run.add(url)

                if url in seen_urls:
                    skipped_previous += 1
                    continue

                title     = row.get("title") or ""
                job_level = row.get("job_level") or ""

                # Pre-LLM seniority check (free)
                if is_clearly_senior(title, job_level):
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)   # don't re-scrape next time
                    continue

                all_jobs.append({
                    "title":       title,
                    "company":     row.get("company"),
                    "location":    row.get("location"),
                    "date_posted": str(row.get("date_posted", "")),
                    "job_url":     url,
                    "job_type":    row.get("job_type"),
                    "job_level":   job_level,
                    "site":        site,
                    "description": row.get("description") or "",
                })

            time.sleep(random.uniform(2, 5))

    print(f"\nTotal new jobs for LLM review: {len(all_jobs)}")
    print(f"Skipped (seen before): {skipped_previous}")
    print(f"Skipped (clearly senior, pre-filter): {skipped_senior_preflight}")
    return all_jobs


# ── LLM filter ────────────────────────────────────────────────────────────────

def should_exclude(description: str, title: str, client: anthropic.Anthropic) -> bool:
    """
    Returns True if the job should be excluded for any of these reasons:
      (a) Written in Dutch or French
      (b) Requires Dutch/French proficiency (not just 'a plus')
      (c) Requires more than 2 years of experience

    Returns False (keep) if:
      - Written in English
      - No hard language requirements
      - Requires 0–2 years, or no experience requirement mentioned
      - Is junior, entry-level, graduate, trainee, or starter role
    """
    if not description:
        blocked = ["nederlandstalig", "franstalig", "dutch required", "french required",
                   "senior", "5 jaar", "5 years", "3 jaar", "3 years", "4 jaar", "4 years"]
        return any(kw in title.lower() for kw in blocked)

    text = description[:2500]

    prompt = (
        "Read this job description and answer YES or NO only.\n\n"
        "Answer YES if ANY of the following are true:\n"
        "1. The description itself is written in Dutch or French (not English)\n"
        "2. The job REQUIRES proficiency or fluency in Dutch or French "
        "(ignore 'a plus', 'nice to have', or 'preferred')\n"
        "3. The job requires MORE THAN 2 years of experience "
        "(e.g. '3+ years', 'minimum 4 years', '5 years of experience')\n"
        "4. The description uses language implying a senior hire: "
        "'proven track record', 'seasoned professional', 'extensive experience', "
        "'deep expertise', 'you will lead a team', 'people management'\n\n"
        "Answer NO if:\n"
        "- The description is in English\n"
        "- No Dutch/French hard requirement (or only 'a plus')\n"
        "- Requires 0–2 years, or no experience requirement mentioned\n"
        "- Is a junior, entry-level, graduate, starter, or trainee role\n\n"
        "Answer only YES or NO.\n\n"
        f"Job title: {title}\n\nJob description:\n{text}\n\nAnswer:"
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip().upper().startswith("YES")
    except Exception as e:
        print(f"     Haiku error: {e} — keeping job by default")
        return False


def filter_jobs(jobs: list[dict], seen_urls: dict[str, str]) -> list[dict]:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    filtered: list[dict] = []
    removed: list[str] = []

    print(f"\nRunning LLM filter on {len(jobs)} jobs...")
    for i, job in enumerate(jobs, 1):
        url   = job.get("job_url", "")
        title = job.get("title", "")

        mark_seen(url, seen_urls)

        if should_exclude(job["description"], title, client):
            removed.append(f"  x {title} @ {job.get('company')}")
        else:
            desc = job["description"]
            job["description_snippet"] = (
                (desc[:DESCRIPTION_SNIPPET_LEN] + "...") if len(desc) > DESCRIPTION_SNIPPET_LEN else desc
            )
            del job["description"]
            filtered.append(job)

        if i % 10 == 0:
            time.sleep(1)

    print(f"LLM excluded {len(removed)} jobs:")
    for r in removed:
        print(r)
    print(f"Jobs passing all filters: {len(filtered)}")
    return filtered


# ── Email delivery ───────────────────────────────────────────────────────────

def send_job_email(jobs: list[dict], last_updated: str) -> None:
    """
    Emails the filtered jobs as a compact JSON payload so the Cowork scheduled
    task can read it from Gmail without needing to fetch an external URL.
    Requires a Gmail App Password (not your main password).
    """
    if not (GMAIL_FROM and GMAIL_APP_PASSWORD):
        print("GMAIL_FROM / GMAIL_APP_PASSWORD not set — skipping email.")
        return

    payload = json.dumps(
        {"last_updated": last_updated, "job_count": len(jobs), "jobs": jobs},
        separators=(",", ":"),
        ensure_ascii=False,
    )

    subject = f"Belgium Job Board | {last_updated[:10]}"
    body = (
        f"Belgium Job Board Update\n"
        f"Jobs: {len(jobs)} | Updated: {last_updated}\n\n"
        f"JOBS_JSON_START\n{payload}\nJOBS_JSON_END"
    )

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        print(f"Email sent: '{subject}' → {GMAIL_TO}")
    except Exception as exc:
        print(f"Email failed (non-fatal, Gist still updated): {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print(f"Job Scraper -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    print("\nFetching previous Gist state...")
    gist_data = fetch_current_gist()
    seen_urls = load_seen_urls(gist_data)
    print(f"Loaded {len(seen_urls)} previously seen URLs")

    print("\nScraping jobs...")
    jobs = collect_all_jobs(seen_urls)

    if not jobs:
        print("\nNo new jobs to process. Updating Gist.")
        update_gist([], seen_urls)
        return

    jobs.sort(key=lambda x: x.get("date_posted", ""), reverse=True)

    filtered = filter_jobs(jobs, seen_urls)

    now_ts = datetime.now(timezone.utc).isoformat()

    print("\nUploading to Gist...")
    update_gist(filtered, seen_urls)

    print("\nSending job email...")
    send_job_email(filtered, now_ts)

    print("\nDone.")


if __name__ == "__main__":
    main()
