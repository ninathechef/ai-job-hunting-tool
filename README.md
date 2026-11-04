# AI-Assisted Job Hunting Tool

A Python-based job discovery pipeline that collects job listings, removes duplicates, filters unsuitable roles, and uses AI to help prioritize opportunities.

## Why I Built This

Searching multiple job boards and repeatedly reviewing similar listings can be time-consuming. I built this project to automate the repetitive parts of job discovery, especially when looking for junior-friendly opportunities. The goal is to make reviewing opportunities more focused, not to automate job applications.

## Features

- **Job collection:** Search supported job boards using [JobSpy](https://github.com/speedyapply/JobSpy), with optional Adzuna API results.
- **Deduplication:** Skip repeated URLs within a run and across runs using state stored in a GitHub Gist.
- **Rule-based filtering:** Exclude roles with configured seniority indicators; optionally exclude Adzuna results above a configured salary threshold.
- **AI-assisted review:** Use Anthropic Claude to assess descriptions for language requirements, experience requirements, and seniority signals.
- **Results:** Write shortlisted jobs and processed-URL state to a Gist, with optional email delivery.
- **Automation:** Run manually through GitHub Actions. You can adapt the workflow for scheduled runs if desired.

> The filtering criteria reflect one example of a junior-focused search. Adapt them to your own preferences before relying on the results.

## How It Works

```text
JobSpy sources + optional Adzuna
              |
              v
       Collect job listings
              |
              v
   Deduplicate by job URL
              |
              v
    Rule-based pre-filter
              |
              v
  Claude description review
              |
              v
       Shortlisted jobs
              |
              +----> GitHub Gist (jobs.json + seen URLs)
              |
              +----> Optional Gmail notification
```

The script marks reviewed or pre-filtered URLs as seen so they are not repeatedly evaluated during the configured retention period. It does **not** submit job applications.

## Tech Stack

- Python, pandas and requests
- JobSpy for job-board collection
- Adzuna API (optional)
- Anthropic Claude API for description evaluation
- GitHub Gist API for results and processing state
- Gmail SMTP (optional)
- GitHub Actions for manual execution

## Project Structure

```text
ai-job-hunting-tool/
├── .github/
│   └── workflows/
│       └── scrape.yml
├── job_scraper.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Getting Started

### 1. Clone and install

```bash
git clone <your-repository-url>
cd ai-job-hunting-tool
python -m venv .venv
```

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Then install dependencies:

```bash
pip install -r requirements.txt
```

### 2. Configure credentials and preferences

Copy `.env.example` to `.env` and edit it for your own search. **The script reads operating-system environment variables; it does not automatically load `.env`.** Load or export the variables before running the script.

For example, on macOS/Linux with Bash, you can load a trusted `.env` containing simple `KEY=value` assignments:

```bash
set -a
source .env
set +a
```

Only source configuration files you created and trust. Alternatively, configure the variables directly in your shell or execution environment.

Required:

| Variable | Purpose |
|---|---|
| `CLAUDE_API_KEY` | Anthropic API credential |
| `GIST_TOKEN` | GitHub token with permission to access/update your Gist |
| `GIST_ID` | Identifier of your own Gist |

Common search settings:

| Variable | Purpose |
|---|---|
| `SEARCH_TERMS` | Comma-separated search keywords |
| `JOB_SOURCES` | Comma-separated JobSpy sources, such as `indeed, linkedin` |
| `LOCATION` | Search location |
| `INDEED_COUNTRY` | Country setting for Indeed |
| `HOURS_OLD` | JobSpy listing age limit in hours |
| `RESULTS_EACH` | Requested results per term/source |
| `CLAUDE_MODEL` | Optional model override; leave unset to use the script default |

Optional integrations:

| Variable | Purpose |
|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_API_KEY` | Enable Adzuna collection when both are set |
| `GMAIL_FROM`, `GMAIL_APP_PASSWORD` | Enable Gmail delivery when both are set |
| `GMAIL_TO` | Optional recipient; defaults to `GMAIL_FROM` |

See `job_scraper.py` for additional settings, including delays, retention, filtering thresholds, and Claude request parameters.

### 3. Create a Gist for state storage

1. Sign in to [GitHub Gist](https://gist.github.com/).
2. Create a new Gist with a file named **`jobs.json`** containing:

   ```json
   {
     "last_updated": null,
     "job_count": 0,
     "jobs": [],
     "seen_urls": {}
   }
   ```

3. Copy that Gist's ID into `GIST_ID`.
4. Configure `GIST_TOKEN` with appropriate access to that Gist.

**Privacy note:** Public Gists are discoverable. Secret Gists are unlisted, **not private**: anyone with the URL can read them. Use a private storage option if your results require access controls. Never commit a real Gist ID, token, or personal results to this repository.

### 4. Run locally

After setting the environment variables:

```bash
python job_scraper.py
```

The script fetches prior state, collects and filters new jobs, updates the Gist, and optionally emails the results. API access, third-party site availability, and configured permissions are required for a successful run.

## GitHub Actions

The included workflow is located at `.github/workflows/scrape.yml` and supports both:

- **Automatic daily execution** through a scheduled trigger
- **Manual execution** through `workflow_dispatch`

To use it in your own repository:

1. Add the required credentials as repository **Actions secrets**: `CLAUDE_API_KEY`, `GIST_TOKEN`, and `GIST_ID`.
2. Add optional Adzuna or Gmail secrets if using those features.
3. Ensure Actions are enabled for the repository and that the workflow file is present on the default branch.
4. For a manual run, open **Actions**, select **Daily Job Scraper**, and choose **Run workflow**.

The scheduled run is configured for `05:00 UTC` each day. This is `06:00` in Belgium during winter time and `07:00` during summer time. GitHub Actions schedules use UTC and can sometimes start several minutes late.


## Customize the Filtering

Two layers of filtering are implemented:

1. **Rule-based pre-filter:** Configurable seniority title keywords and job levels. For Adzuna results, a maximum salary threshold is also applied.
2. **Claude review:** The prompt in `should_exclude()` excludes descriptions written in Dutch or French, roles requiring those languages, roles requiring more than two years of experience, and clearly senior roles.

The Claude prompt is **defined in Python**, not fully configurable through `.env.example`. Edit the prompt to match your languages, experience level, and search priorities. AI classifications can be imperfect; review the original job listing before making decisions.

## Security and Limitations

- Keep credentials in environment variables or GitHub Actions Secrets, not source files.
- Do not commit `.env`, `jobs.json`, personal results, or execution logs.
- `.gitignore` does not remove files already committed to Git history; inspect history before publishing.
- The Gist contains shortlisted jobs, processed URLs, and update timestamps. Choose its visibility deliberately.
- The current code treats some source/API errors as empty results and keeps jobs when Claude evaluation fails. Review logs and output rather than assuming a run was fully successful.
- Deduplication is based on job URLs, so the same position at different URLs may appear more than once.
- Salary filtering is a simple threshold for Adzuna results and does not normalize currencies or pay periods.
- Respect each data source's terms, robots/access rules where applicable, rate limits, and API usage requirements.
- Third-party APIs may incur costs. This project does not guarantee complete listings or accurate job suitability assessments.

## Disclaimer

This is a personal learning and automation project shared to demonstrate a reproducible approach to AI-assisted job discovery. It is not affiliated with the job boards or API providers used. Adapt and test it for your own needs before relying on it.
