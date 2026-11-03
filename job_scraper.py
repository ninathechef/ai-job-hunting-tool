"""AI-assisted job discovery pipeline configured with environment variables."""
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


def required_env(name: str) -> str:
    """Read a required environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not configured.")
    return value


def optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def int_env(name: str, default: int) -> int:
    value = optional_env(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable '{name}' must be an integer.") from exc


def float_env(name: str, default: float) -> float:
    value = optional_env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable '{name}' must be a number.") from exc


def csv_env(name: str, default: str = "", *, lowercase: bool = False) -> list[str]:
    """Read a comma-separated environment variable as a cleaned list."""
    values = [item.strip() for item in optional_env(name, default).split(",") if item.strip()]
    return [item.lower() for item in values] if lowercase else values


# Credentials
CLAUDE_API_KEY = required_env("CLAUDE_API_KEY")
GIST_TOKEN = required_env("GIST_TOKEN")
GIST_ID = required_env("GIST_ID")

# Job search
SEARCH_TERMS = csv_env("SEARCH_TERMS", "data analyst,business analyst")
JOB_SOURCES = csv_env("JOB_SOURCES", "indeed", lowercase=True)
LOCATION = optional_env("LOCATION", "Belgium")
INDEED_COUNTRY = optional_env("INDEED_COUNTRY", "belgium")
HOURS_OLD = int_env("HOURS_OLD", 24)
RESULTS_EACH = int_env("RESULTS_EACH", 20)
DESCRIPTION_FORMAT = optional_env("DESCRIPTION_FORMAT", "markdown")
DESCRIPTION_SNIPPET_LEN = int_env("DESCRIPTION_SNIPPET_LEN", 600)
SEEN_URLS_TTL_DAYS = int_env("SEEN_URLS_TTL_DAYS", 7)
LINKEDIN_FETCH_DESCRIPTION = optional_env("LINKEDIN_FETCH_DESCRIPTION", "false").lower() in {
    "1", "true", "yes", "on"
}

# Optional Adzuna
ADZUNA_APP_ID = optional_env("ADZUNA_APP_ID")
ADZUNA_API_KEY = optional_env("ADZUNA_API_KEY")
ADZUNA_ENABLED = bool(ADZUNA_APP_ID and ADZUNA_API_KEY)
ADZUNA_COUNTRY = optional_env("ADZUNA_COUNTRY", "be")
ADZUNA_PAGE = int_env("ADZUNA_PAGE", 1)
ADZUNA_MAX_DAYS_OLD = int_env("ADZUNA_MAX_DAYS_OLD", 7)
ADZUNA_BASE_URL = optional_env("ADZUNA_BASE_URL", "https://api.adzuna.com/v1/api/jobs")

# Filtering
SENIOR_TITLE_KEYWORDS = csv_env(
    "SENIOR_TITLE_KEYWORDS",
    "senior,sr.,principal,head of,director,vice president,vp,chief,c-level,staff engineer,distinguished,architect",
    lowercase=True,
)
SENIOR_JOB_LEVELS = set(csv_env(
    "SENIOR_JOB_LEVELS", "mid-senior level,senior,director,executive,management", lowercase=True
))
MAX_JUNIOR_SALARY = float_env("MAX_JUNIOR_SALARY", 55000)

# Claude
CLAUDE_MODEL = optional_env("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_MAX_TOKENS = int_env("CLAUDE_MAX_TOKENS", 5)
CLAUDE_DESCRIPTION_LIMIT = int_env("CLAUDE_DESCRIPTION_LIMIT", 2500)
LLM_BATCH_SIZE = int_env("LLM_BATCH_SIZE", 10)
LLM_BATCH_SLEEP_SECONDS = float_env("LLM_BATCH_SLEEP_SECONDS", 1)

# Requests and delays
HTTP_TIMEOUT_SECONDS = int_env("HTTP_TIMEOUT_SECONDS", 15)
GIST_UPDATE_TIMEOUT_SECONDS = int_env("GIST_UPDATE_TIMEOUT_SECONDS", 30)
SOURCE_SLEEP_MIN_SECONDS = float_env("SOURCE_SLEEP_MIN_SECONDS", 2)
SOURCE_SLEEP_MAX_SECONDS = float_env("SOURCE_SLEEP_MAX_SECONDS", 5)
ADZUNA_SLEEP_MIN_SECONDS = float_env("ADZUNA_SLEEP_MIN_SECONDS", 1)
ADZUNA_SLEEP_MAX_SECONDS = float_env("ADZUNA_SLEEP_MAX_SECONDS", 2)

# Email
GMAIL_FROM = optional_env("GMAIL_FROM")
GMAIL_APP_PASSWORD = optional_env("GMAIL_APP_PASSWORD")
GMAIL_TO = optional_env("GMAIL_TO") or GMAIL_FROM
SMTP_HOST = optional_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int_env("SMTP_PORT", 465)
EMAIL_SUBJECT_PREFIX = optional_env("EMAIL_SUBJECT_PREFIX", "Job Board")


def validate_configuration() -> None:
    """Validate search and timing configuration."""
    if not SEARCH_TERMS:
        raise RuntimeError("SEARCH_TERMS must contain at least one search term.")
    if not JOB_SOURCES and not ADZUNA_ENABLED:
        raise RuntimeError("Configure at least one JOB_SOURCES value or enable Adzuna.")
    if SOURCE_SLEEP_MIN_SECONDS > SOURCE_SLEEP_MAX_SECONDS:
        raise RuntimeError("SOURCE_SLEEP_MIN_SECONDS cannot be greater than SOURCE_SLEEP_MAX_SECONDS.")
    if ADZUNA_SLEEP_MIN_SECONDS > ADZUNA_SLEEP_MAX_SECONDS:
        raise RuntimeError("ADZUNA_SLEEP_MIN_SECONDS cannot be greater than ADZUNA_SLEEP_MAX_SECONDS.")


def gist_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {GIST_TOKEN}", "Accept": "application/vnd.github+json"}


def fetch_current_gist() -> dict:
    """Retrieve existing job and seen-URL state."""
    try:
        response = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers=gist_headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        files = response.json().get("files", {})
        raw_content = files.get("jobs.json", {}).get("content", "{}")
        return json.loads(raw_content)
    except Exception as exc:
        print(f"Warning: could not fetch the current Gist state: {exc}")
        return {}


def update_gist(jobs: list[dict], seen_urls: dict[str, str]) -> str:
    """Persist the latest filtered jobs and deduplication state."""
    payload = {
        "files": {
            "jobs.json": {
                "content": json.dumps(
                    {
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                        "job_count": len(jobs),
                        "jobs": jobs,
                        "seen_urls": seen_urls,
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            }
        }
    }
    response = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=gist_headers(),
        json=payload,
        timeout=GIST_UPDATE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    gist_url = response.json().get("html_url", "")
    print("Gist updated successfully.")
    return gist_url


def load_seen_urls(gist_data: dict) -> dict[str, str]:
    """Remove previously seen URLs older than the configured TTL."""
    seen_urls = gist_data.get("seen_urls", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_URLS_TTL_DAYS)).date()
    pruned_urls = {url: seen_date for url, seen_date in seen_urls.items() if seen_date >= str(cutoff)}
    removed_count = len(seen_urls) - len(pruned_urls)
    if removed_count:
        print(f"Pruned {removed_count} expired entries from seen_urls")
    return pruned_urls


def mark_seen(url: str, seen_urls: dict[str, str]) -> None:
    if url:
        seen_urls[url] = str(datetime.now(timezone.utc).date())


def is_clearly_senior(title: str, job_level: str) -> bool:
    """Apply fast seniority pre-filtering."""
    title_lower = (title or "").lower()
    level_lower = (job_level or "").lower().strip()
    if level_lower in SENIOR_JOB_LEVELS:
        return True
    return any(keyword in title_lower for keyword in SENIOR_TITLE_KEYWORDS)


def scrape_site(search_term: str, site: str) -> pd.DataFrame:
    """Collect listings from one JobSpy source."""
    try:
        print(f" -> [{site}] '{search_term}'")
        scrape_arguments = {
            "site_name": site,
            "search_term": search_term,
            "location": LOCATION,
            "results_wanted": RESULTS_EACH,
            "hours_old": HOURS_OLD,
            "description_format": DESCRIPTION_FORMAT,
            "verbose": 0,
        }
        if site == "indeed":
            scrape_arguments["country_indeed"] = INDEED_COUNTRY
        if site == "linkedin":
            scrape_arguments["linkedin_fetch_description"] = LINKEDIN_FETCH_DESCRIPTION
        dataframe = scrape_jobs(**scrape_arguments)
        print(f" Found {len(dataframe)} jobs")
        return dataframe
    except Exception as exc:
        print(f" Source error: {exc}")
        return pd.DataFrame()


def scrape_adzuna(search_term: str) -> list[dict]:
    """Collect and normalize listings from optional Adzuna API."""
    if not ADZUNA_ENABLED:
        return []
    try:
        url = f"{ADZUNA_BASE_URL}/{ADZUNA_COUNTRY}/search/{ADZUNA_PAGE}"
        parameters = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_API_KEY,
            "what": search_term,
            "results_per_page": RESULTS_EACH,
            "max_days_old": ADZUNA_MAX_DAYS_OLD,
        }
        response = requests.get(
            url, params=parameters, headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        jobs: list[dict] = []
        for result in response.json().get("results", []):
            created = result.get("created") or ""
            jobs.append({
                "title": result.get("title"),
                "company": result.get("company", {}).get("display_name"),
                "location": result.get("location", {}).get("display_name"),
                "date_posted": created[:10] if created else "",
                "job_url": result.get("redirect_url"),
                "job_type": None,
                "job_level": None,
                "site": "adzuna",
                "description": result.get("description") or "",
                "salary_max": result.get("salary_max"),
            })
        print(f" Found {len(jobs)} jobs")
        return jobs
    except Exception as exc:
        print(f" Adzuna error: {exc}")
        return []


def collect_all_jobs(seen_urls: dict[str, str]) -> list[dict]:
    """Collect new jobs, removing duplicates and clearly senior roles."""
    all_jobs: list[dict] = []
    seen_this_run: set[str] = set()
    skipped_previous = 0
    skipped_senior_preflight = 0

    for search_term in SEARCH_TERMS:
        if ADZUNA_ENABLED:
            print(f" -> [adzuna] '{search_term}'")
            adzuna_jobs = scrape_adzuna(search_term)
            for job in adzuna_jobs:
                url = job.get("job_url") or ""
                if not url or url in seen_this_run:
                    continue
                seen_this_run.add(url)
                if url in seen_urls:
                    skipped_previous += 1
                    continue
                if is_clearly_senior(job.get("title") or "", job.get("job_level") or ""):
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)
                    continue
                salary_max = job.get("salary_max")
                if salary_max is not None and float(salary_max) > MAX_JUNIOR_SALARY:
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)
                    continue
                all_jobs.append(job)
            time.sleep(random.uniform(ADZUNA_SLEEP_MIN_SECONDS, ADZUNA_SLEEP_MAX_SECONDS))

        for site in JOB_SOURCES:
            dataframe = scrape_site(search_term, site)
            if dataframe.empty:
                continue
            for _, row in dataframe.iterrows():
                url = row.get("job_url") or ""
                if not url or url in seen_this_run:
                    continue
                seen_this_run.add(url)
                if url in seen_urls:
                    skipped_previous += 1
                    continue
                title = row.get("title") or ""
                job_level = row.get("job_level") or ""
                if is_clearly_senior(title, job_level):
                    skipped_senior_preflight += 1
                    mark_seen(url, seen_urls)
                    continue
                all_jobs.append({
                    "title": title,
                    "company": row.get("company"),
                    "location": row.get("location"),
                    "date_posted": str(row.get("date_posted", "")),
                    "job_url": url,
                    "job_type": row.get("job_type"),
                    "job_level": job_level,
                    "site": site,
                    "description": row.get("description") or "",
                })
            time.sleep(random.uniform(SOURCE_SLEEP_MIN_SECONDS, SOURCE_SLEEP_MAX_SECONDS))

    print(f"\nTotal new jobs for LLM review: {len(all_jobs)}")
    print(f"Skipped because previously seen: {skipped_previous}")
    print(f"Skipped by rule-based seniority filter: {skipped_senior_preflight}")
    return all_jobs


def should_exclude(description: str, title: str, client: anthropic.Anthropic) -> bool:
    """Use Claude to check language, experience, and seniority requirements."""
    if not description:
        fallback_keywords = [
            "nederlandstalig", "franstalig", "dutch required", "french required",
            "senior", "3 jaar", "3 years", "4 jaar", "4 years", "5 jaar", "5 years",
        ]
        return any(keyword in title.lower() for keyword in fallback_keywords)

    text = description[:CLAUDE_DESCRIPTION_LIMIT]
    prompt = (
        "Read this job description and answer YES or NO only.\n\n"
        "Answer YES if ANY of the following are true:\n"
        "1. The description itself is written in Dutch or French (not English)\n"
        "2. The job REQUIRES proficiency or fluency in Dutch or French. "
        "Ignore language skills listed only as a plus, preference, or nice-to-have.\n"
        "3. The job requires MORE THAN 2 years of experience, such as 3+ years, "
        "minimum 4 years, or 5 years of experience.\n"
        "4. The description clearly implies a senior hire through requirements "
        "such as a proven track record, extensive experience, people management, "
        "or leading a team.\n\n"
        "Answer NO if:\n"
        "- The description is in English\n"
        "- Dutch or French is not a hard requirement\n"
        "- The job requires 0 to 2 years of experience, or no experience requirement is mentioned\n"
        "- The role is junior, entry-level, graduate, starter, or trainee\n\n"
        "Answer only YES or NO.\n\n"
        f"Job title: {title}\n\n"
        f"Job description:\n{text}\n\n"
        "Answer:"
    )
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = message.content[0].text.strip().upper()
        return answer.startswith("YES")
    except Exception as exc:
        print(f" Claude error: {exc}. Keeping the job by default.")
        return False


def filter_jobs(jobs: list[dict], seen_urls: dict[str, str]) -> list[dict]:
    """Evaluate each collected job with Claude and retain relevant listings."""
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    filtered_jobs: list[dict] = []
    removed_jobs: list[str] = []
    print(f"\nRunning LLM filter on {len(jobs)} jobs...")
    for index, job in enumerate(jobs, start=1):
        url = job.get("job_url") or ""
        title = job.get("title") or ""
        description = job.get("description") or ""
        mark_seen(url, seen_urls)
        if should_exclude(description, title, client):
            removed_jobs.append(f" x {title} @ {job.get('company')}")
        else:
            job["description_snippet"] = (
                description[:DESCRIPTION_SNIPPET_LEN] + "..."
                if len(description) > DESCRIPTION_SNIPPET_LEN else description
            )
            job.pop("description", None)
            filtered_jobs.append(job)
        if LLM_BATCH_SIZE > 0 and index % LLM_BATCH_SIZE == 0:
            time.sleep(LLM_BATCH_SLEEP_SECONDS)
    print(f"LLM excluded {len(removed_jobs)} jobs:")
    for removed_job in removed_jobs:
        print(removed_job)
    print(f"Jobs passing all filters: {len(filtered_jobs)}")
    return filtered_jobs


def send_job_email(jobs: list[dict], last_updated: str) -> None:
    """Optionally email a compact JSON payload of filtered jobs."""
    if not GMAIL_FROM or not GMAIL_APP_PASSWORD:
        print("Email credentials are not configured. Skipping email delivery.")
        return
    payload = json.dumps(
        {"last_updated": last_updated, "job_count": len(jobs), "jobs": jobs},
        separators=(",", ":"), ensure_ascii=False,
    )
    subject = f"{EMAIL_SUBJECT_PREFIX} | {last_updated[:10]}"
    body = (
        f"{EMAIL_SUBJECT_PREFIX} Update\nJobs: {len(jobs)}\nUpdated: {last_updated}\n\n"
        f"JOBS_JSON_START\n{payload}\nJOBS_JSON_END"
    )
    message = MIMEMultipart()
    message["Subject"] = subject
    message["From"] = GMAIL_FROM
    message["To"] = GMAIL_TO
    message.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(message)
        print(f"Email sent: '{subject}' -> {GMAIL_TO}")
    except Exception as exc:
        print(f"Email delivery failed. The Gist was still updated successfully: {exc}")


def main() -> None:
    """Run the complete job discovery pipeline."""
    validate_configuration()
    print("=" * 60)
    print(f"Job Discovery Pipeline -- {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 60)
    print(f"Location: {LOCATION}")
    print(f"Search terms: {', '.join(SEARCH_TERMS)}")
    if JOB_SOURCES:
        print(f"Configured sources: {', '.join(JOB_SOURCES)}")
    print(f"Adzuna integration: {'enabled' if ADZUNA_ENABLED else 'disabled'}")
    print("\nFetching previous Gist state...")
    gist_data = fetch_current_gist()
    seen_urls = load_seen_urls(gist_data)
    print(f"Loaded {len(seen_urls)} previously seen URLs")
    print("\nCollecting jobs...")
    jobs = collect_all_jobs(seen_urls)
    if not jobs:
        print("\nNo new jobs to process. Updating Gist state.")
        update_gist([], seen_urls)
        return
    jobs.sort(key=lambda job: job.get("date_posted", ""), reverse=True)
    filtered_jobs = filter_jobs(jobs, seen_urls)
    current_timestamp = datetime.now(timezone.utc).isoformat()
    print("\nUploading results to Gist...")
    update_gist(filtered_jobs, seen_urls)
    print("\nSending job email...")
    send_job_email(filtered_jobs, current_timestamp)
    print("\nDone.")


if __name__ == "__main__":
    main()
