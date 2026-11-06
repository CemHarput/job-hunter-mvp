# Claude Code Job Hunter MVP v0.1

Local Python/SQLite job collector with Claude Code agent instructions and optional SMTP report delivery. **No invented job listings.** Default sources are empty until you add verified company ATS board IDs.

## Setup (Python 3.11+)

```bash
cd job-hunter-mvp
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install -e '.[test]'
python -m pytest -q
```

## Add real public ATS sources

Edit `config/sources.yaml` with actual board identifiers verified against the company's careers site. Supported:

```yaml
greenhouse:
  - token: verified-board-token
    company: Verified Company
lever: []
ashby: []
```

The example token is **not real**. Don't use it until replaced. For Ashby use the job board slug; for Lever use the site slug. To verify, open the corresponding public ATS endpoint described in `jobhunter/collectors.py`.

## Run

```bash
python -m jobhunter scan
python -m jobhunter list
python -m jobhunter mark-applied --company 'Company' --title 'Senior Backend Engineer' --url 'https://example.com/apply'
```

Review and baseline workflow:

```bash
python -m jobhunter scan --baseline            # first run: mark everything currently posted as reported, list nothing
python -m jobhunter scan --max-age-days 30     # skip jobs published more than 30 days ago (undated jobs are kept)
python -m jobhunter review --job-id 12 --status VERIFIED_TR --reviewer me   --evidence-url https://example.com/careers/tr --evidence-quote 'exact quote' --notes '...'
```

`review` statuses: `VERIFIED_TR` (requires an https `--evidence-url` and a non-empty `--evidence-quote`), `GLOBAL_UNCONFIRMED` (alias `REMOTE_WORLDWIDE_UNCONFIRMED`), `UNKNOWN`, `INELIGIBLE`. Reviews are stored in `review_events` and `jobs.reviewed_status`; later scans never overwrite them. Heuristic `excluded` still wins.

Every scan lists all jobs not yet reported (not only first-seen in that scan). Jobs are marked reported only after the report is written (and the email sent, with `--email`); on email failure the command exits 1 and the jobs reappear next scan. Each report is also archived in `reports/archive/`. Heuristically `ineligible` jobs are listed in a separate "Elenen ilanlar" section so they are not silently hidden. `minimum_contract_months` rejects only postings that explicitly state a shorter contract; `low_priority_companies` lowers the score by 20 and labels the job; `exclude_company_policy` supports only `all_roles`. Existing databases are migrated automatically (old jobs are marked `legacy`-reported).

Open `reports/latest.md`. Initial scan of a newly configured board finds *first-seen*, not necessarily newly published, jobs. Subsequent scans only report not-yet-reported records. Excluded companies and applied jobs never appear in the new-job report. `config/profile.yaml` controls filters.

### Location filter
`config/locations.yaml` is an editable table (open / regional / turkiye / restricted places, US state codes). A job is marked `ineligible` only when **every** alternative in its location (split on `|`, `;`, `/`, " or ") is a restricted place or on-site/hybrid without remote; worldwide/global stays `global_unconfirmed`, EMEA/Europe stays `global_unconfirmed` ("Bölgesel, Türkiye doğrulanmadı"), Türkiye + remote/Ankara is `candidate_tr`, bare "Remote"/empty/unrecognised stays `unknown`. Nothing is ever `VERIFIED_TR` from location. If the file is missing a warning is printed and no location elimination happens.

```bash
python -m jobhunter locations --unclassified    # location strings the table cannot classify yet (add them to the YAML)
python -m jobhunter reevaluate --dry-run        # offline: preview how stored jobs change under current rules
python -m jobhunter reevaluate                  # apply; updates only status/evidence/score/technologies, never review/reported fields
python -m jobhunter list --rejected [--limit 0] # all ineligible jobs (reported or not) with evidence
```
In the report, location-based rejections are grouped as `- United States: N ilan`; the full per-job list is written to `reports/archive/scan-<stamp>-rejected.md`. Rejections are never hidden.

### Search profile (EMEA / remote / B2B-contract)
`config/profile.yaml` has a `search:` block (when present, search mode is on; delete it to get the old rules). A job is listed only if it passes four tests: **role** (`search.roles`: `include` words in the title, `exclude_always` e.g. sales/QA/manager/mechanical, `exclude` e.g. frontend/iOS unless a `rescue` word such as backend/python/platform is in the title; these lists replace the built-in defaults), **remote** (location says remote; on-site/hybrid fails), **region** (`search.regions: [EMEA]`, defined in `config/locations.yaml` `regions:`; EMEA-wide, worldwide or any EMEA country/city passes, US/Canada/APAC/Americas fail, a bare "Remote" is "Bölge belirsiz" unless the description says worldwide/remote-Europe) and **engagement** (`employment_type` field = contract/contractor/freelance/B2B, or a contextual phrase such as "B2B contract", "independent contractor", "Type: Contract", "12 Month FTC"; "B2B SaaS", "smart contract" and negated phrases do not count). Remote + Türkiye jobs skip the engagement test (`turkiye_any_engagement`) and get their own table. Country-based EMEA jobs (UK, Germany, Ukraine, South Africa ...) pass but are marked "ülke bazlı EMEA, B2B ile Türkiye'den uygunluk doğrulanmalı"; nothing is ever `VERIFIED_TR`. A stated contract shorter than `required.minimum_contract_months` is still rejected; unknown length stays unknown. Ranking: Türkiye > EMEA-wide/worldwide > country-based; pay is quoted from the source (`salary`, Ashby compensation, or an amount near a pay word) or shown as "Doğrulanmadı" and never affects ranking.

The report shows `## EMEA · Remote · B2B/Contract (N)` (company, title, location, engagement evidence, pay, source, first seen, note, score). Rejected jobs are grouped as `- EMEA dışı: United States: N ilan`, `- B2B/Contract sinyali yok: N ilan` ... and listed in full in `*-rejected.md` / `list --rejected`. `python -m jobhunter reevaluate --dry-run` prints `Arama profili: geçen K / M` plus the top reject groups. `required.worldwide_or_b2b` is deprecated (still works alone, prints a warning; combining it with `search:` is an error).

**Data availability:** ATS boards (Greenhouse/Lever/Ashby) rarely state B2B/contract and mostly publish permanent roles, so with only the configured boards the list will be empty or very short. Ashby `employmentType` and Lever `categories.commitment` are stored in `jobs.employment_type` (new DB columns, added automatically; stored rows get it on the next `scan`). The planned `job-discovery-sources` feature (aggregator feeds) is expected to fill `employment_type` (raw source value) and `salary` (raw source text) so the list becomes useful; this feature works without them (None).

## Optional email

Copy `.env.example` to `.env`, set SMTP values (prefer an app password); `scan` loads the repo-root `.env` automatically (or `--env-file PATH`) without overriding variables already set in your environment. Manual loading on macOS/Linux still works:

```bash
set -a; source .env; set +a
python -m jobhunter scan --email
```

`--email` is **opt-in**. If you want a daily scheduled scan, create a local cron/Task Scheduler job that activates your venv and runs `python -m jobhunter scan --email`. Schedule only after testing SMTP delivery. A failed SMTP send does not undo the local report, but the jobs stay unreported.

## Claude Code

Run `claude` in the project root. `CLAUDE.md` provides operating rules and `.claude/agents/` contains five agent definitions. Example request: “Run the scan, inspect only new results, use eligibility-checker to investigate Türkiye employment eligibility with employer citations, and prepare a shortlist. Don't email recruiters or submit applications.”

## Constraints

The program does not autonomously find new companies across the web; you maintain the ATS board list or use `job-scout` to research additions. Automated country checks are **not legal eligibility verification**. Salary and >=6-month contract claims require manual evidence. Read `docs/ARCHITECTURE.md` for known limitations.
