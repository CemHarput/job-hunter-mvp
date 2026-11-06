# Architecture and verification

`collectors.py` -> public ATS feeds -> `evaluate.py` conservative text heuristics -> `db.py` SQLite upsert -> `cli.py` report -> optional SMTP email.

Tables:
- `jobs`: `(source,source_id)` unique; first/last seen, source date with `date_kind`, location, description, heuristic eligibility, score, `reviewed_status`/`reviewed_at` (set only by `review`, never by scans), `reported_at`/`reported_via` (report, email, baseline, legacy).
- `applications`: user-confirmed application state and timestamp, optional link to a discovered job.
- `scan_runs`: per-run counts and errors.
- `review_events`: human/agent review audit trail written by `python -m jobhunter review`; VERIFIED_TR requires an https evidence URL and quote.

## Evidence standard
`candidate_tr` means a regex saw Türkiye near remote, NOT legal confirmation. No rule-only result is `VERIFIED_TR`. `global_unconfirmed` and `unknown` require employer evidence. A posting date may be a modified date or created date; never call it the original publication date unless the feed explicitly says so.

## Current MVP limitations
- ATS feeds require explicitly configured verified board IDs; no broad web search integration or scheduled daily runner is included yet.
- Regex scoring is a triage aid; it can miss valid roles or misread geography. It cannot validate salary, contract duration, hiring entities, or live application form availability.
- Jobs removed from feeds are not automatically marked closed (avoid false closures on partial outages).
- Email reports are opt-in via `--email`; local SMTP secrets stay in `.env`, which `scan` loads (existing environment variables win).
- Deduplication uses source ID and application company/title or URL; cross-board duplicates may still appear. Add URL normalization and cross-source identity resolution in v0.2.
- First scan marks all existing board jobs as first-seen; use `scan --baseline` or `--max-age-days` to avoid a flood. First-seen does not mean newly published.
- Geography/contract-length regexes are heuristics. Title/geography rejections are shown in the report's "Elenen ilanlar" section. Abbreviations (US, UK, EU) are matched case-sensitively, so lowercase "us only" is missed on purpose.
- No automatic job applications, no browser automation, no live Claude API calls. Claude Code reads `.claude/agents` when opened from project root.

## Location filter
`jobhunter/locations.py` classifies the structured `location` field using `config/locations.yaml` (data-driven; `load_locations` validates it and fails with `Locations error`). Outcomes: `turkiye`, `open`, `regional`, `tr_unknown`, `unclassified`, `remote_bare`, `empty`, `restricted`; the job takes the best alternative, so it is restricted only if all alternatives are. `evaluate(job, profile, locations=None)` precedence: excluded > irrelevant > text `NEGATIVE` > location restricted > contract length > location/text positive logic > low-priority penalty. Without a table (`None`, or a missing `config/locations.yaml`) a built-in table with no restricted country/city entries is used, so nothing is eliminated by country or city; bare Hybrid/On-site/In-office locations are still eliminated, because that rule lives in code. Words the table does not know (e.g. "excluding" in "EMEA (excluding Turkey)") make the location `unclassified` rather than a Türkiye or restricted match. Restricted evidence starts with `Location restricted [<labels>]` and the report groups on that prefix. Limits: "Berlin, Remote"-style strings are read as separate alternatives; locations in the job title are not parsed; unknown words never reject (they are listed by `locations --unclassified` and in the report footer). `reevaluate` rewrites only heuristic fields (`db.update_heuristics`). No schema change.

## Search profile
With `search:` in `config/profile.yaml`, `evaluate` takes the search path (`_evaluate_search`) after the exclusion check: role policy (`role_ok`) > text geography (`NEGATIVE`: US/Canada/on-site reject; UK/EU/EEA only make the job country-based) > remote > region (`locations.region_of`, `regions` section of `config/locations.yaml`; kinds turkiye > open > regional > country > unknown > outside) > engagement (`evaluate.engagement`: `employment_type` field, then contextual text) > stated contract length (`minimum_contract_months`) > score (25 + region bonus 30/20/15/5 + 10 for engagement evidence + technology + seniority; pay never scores). Rejections start with `Search profile [<label>]` and are grouped by `cli._group`. The result dict gains `search={kind, labels, engagement, pay, note, section}`, which `run_scan` recomputes for the candidate rows to render the report tables. Without `search:` the old path runs unchanged, including the deprecated `required.worldwide_or_b2b` gate (`validate_profile` warns; both together is an error). Status stays `unknown` for country-based jobs, `global_unconfirmed` for EMEA-wide/worldwide, `candidate_tr` only when Türkiye is explicit; never `VERIFIED_TR`.

Job dict data contract (filled by collectors, stored in `jobs.employment_type`/`jobs.salary`, both TEXT, added by `db.migrate`): `employment_type` = raw source value (str, list of str or None; Ashby `employmentType`, Lever `categories.commitment`; discovery feeds should pass Remotive `job_type`, Himalayas `employmentType`, Remote OK `tags`, Arbeitnow `job_types`), `salary` = raw source text or None. Optional report keys read with `.get`: `source_name`, `source_url`, `apply_url` (not stored yet). Limitation: with only ATS boards the list is expected to be (nearly) empty.
