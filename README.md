# AI-Assisted Job Hunting Tool

A Python automation pipeline that collects, filters, and evaluates job opportunities to make job searching more efficient.

I built this project to reduce the repetitive work involved in browsing job boards. Instead of manually reviewing hundreds of listings, the pipeline collects jobs from multiple sources, removes duplicates and clearly irrelevant positions, and uses AI to evaluate which opportunities are better suited to a junior candidate profile.

## Features

- Collects job listings from multiple sources
- Removes duplicate and previously processed jobs
- Filters out clearly senior positions
- Uses Claude to evaluate job requirements
- Identifies opportunities suitable for junior candidates
- Stores processing state to avoid reviewing the same jobs repeatedly
- Supports email delivery of filtered results
- Supports automated runs with GitHub Actions

## How It Works

```text
Job Sources
    │
    ▼
Job Collection
    │
    ▼
Deduplication
    │
    ▼
Seniority Pre-Filtering
    │
    ▼
AI-Based Evaluation
    │
    ▼
Relevant Opportunities
    │
    ├──► JSON / Gist
    │
    └──► Email
```

The pipeline combines rule-based filtering with LLM-assisted evaluation.

Predictable criteria, such as obvious seniority indicators, are handled first using traditional filtering. The remaining job descriptions are then evaluated by Claude for requirements that require more contextual analysis.

## Tech Stack

- **Python** — core pipeline and processing logic
- **JobSpy** — job listing collection
- **Adzuna API** — additional job listings
- **Anthropic Claude** — AI-assisted job evaluation
- **GitHub Actions** — scheduled automation
- **GitHub Gist** — processing state and result storage
- **Gmail** — optional email delivery

## Project Structure

```text
ai-job-hunting-tool/
│
├── .github/
│   └── workflows/
│       └── scrape.yml
│
├── job_scraper.py
├── requirements.txt
├── .gitignore
└── README.md
```

## Setup

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd ai-job-hunting-tool
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Depending on the features used, the application may require:

```text
CLAUDE_API_KEY
GIST_TOKEN
GIST_ID
ADZUNA_APP_ID
ADZUNA_API_KEY
GMAIL_FROM
GMAIL_APP_PASSWORD
```

For local development, configure these as environment variables.

For GitHub Actions, add the required values under:

**Repository Settings → Secrets and variables → Actions → New repository secret**

Never commit real credentials, API keys, passwords, or authentication tokens to the repository.

### 5. Run the scraper

```bash
python job_scraper.py
```

## Automation

The project includes a GitHub Actions workflow:

```text
.github/workflows/scrape.yml
```

Once the required secrets are configured, the workflow can run the pipeline automatically.

It can also be triggered manually from the repository's **Actions** tab.

## Security

Sensitive configuration is kept outside the source code using:

- Environment variables
- GitHub Actions Secrets

If you fork or reuse this project, create your own API credentials and configure them separately.

## Disclaimer

This is a personal automation and learning project.

If you reuse or modify it, make sure your use of third-party websites, APIs, and job-listing services complies with their applicable terms, policies, and rate limits.
