# Request: job-discovery-sources

User need (verbatim intent): "sources.yaml'ı ben doldurmam mantıklı değil; sistemin benim yerime çeşitli kaynaklardan bana uygun işleri araması gerekiyor." LinkedIn is not an option (automated scraping violates its terms; CLAUDE.md allows public endpoints only).

Add automatic job discovery on top of the existing pipeline (collect -> evaluate -> upsert -> report; all in jobhunter/collectors.py, evaluate.py, db.py, cli.py):

1. **Aggregator collectors** (no company list needed) for public feeds. Probed by the coordinator on 2026-11-06 (HTTP 200, no auth):
   - Remotive: `https://remotive.com/api/remote-jobs?category=software-dev` -> JSON `jobs[]` keys: id, url, title, company_name, category, tags, job_type, publication_date, candidate_required_location, salary, description. Legal notice in response: link back to the Remotive URL AND mention Remotive as a source; do NOT submit Remotive jobs to third-party sites (Jooble, Google Jobs, LinkedIn Jobs); jobs are delayed 24h.
   - Remote OK: `https://remoteok.com/api` -> JSON list, first element is a legal/metadata object, then jobs with keys: slug, id, epoch, date, company, position, tags, description, location, salary_min, salary_max, apply_url, url. ToS: link back to the Remote OK URL (follow) and mention Remote OK as the source.
   - Arbeitnow: `https://www.arbeitnow.com/api/job-board-api` -> JSON `data[]` (slug, company_name, title, description, ...); ~2.5 MB response.
   - We Work Remotely: RSS e.g. `https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss` (RSS 2.0).
   - Himalayas: `https://himalayas.app/jobs/api?limit=N` -> JSON `jobs[]` keys: title, excerpt, companyName, companySlug, employmentType, minSalary, maxSalary, seniority, locationRestrictions, timezoneRestrictions, categories, description, pubDate, expiryDate, applicationLink, guid; cursor pagination via `nextCursor`/`?cursor=` (offset deprecated).
   - Hacker News "Who is hiring": user `whoishiring` (Firebase API `https://hacker-news.firebaseio.com/v0/user/whoishiring.json`) -> submitted ids; monthly thread comments are free-text job posts.
   Verify field names against real responses again during implementation; do not invent fields.
2. **Source attribution**: every report entry must show the source name and link back to the original aggregator URL (terms require it). Applying link must be the real URL from the feed; never invent apply links, dates, salaries.
3. **Board auto-discovery**: when an aggregator job's URL or apply link points to Greenhouse/Lever/Ashby (e.g. boards.greenhouse.io/<token>, jobs.lever.co/<site>, jobs.ashbyhq.com/<board>), propose that board as a `sources.yaml` candidate (a new `python -m jobhunter discover` command or a section in the report). Never auto-edit sources.yaml silently; adding requires verifying the public endpoint returns 200/valid JSON and the company name matches. Respect existing exclusions.
4. **Config**: enable/disable each aggregator in `config/sources.yaml` (new `aggregators:` section; default on for the six sources, with per-source options such as category and max age). Keep existing greenhouse/lever/ashby lists working.
5. **Behavior**: dedupe by (source, source_id) as today and also across sources (same company+title+similar location from two aggregators or an aggregator + ATS board should not be reported twice; keep the first, record the other as an alternate source URL or skip); aggregator-reported location text ("Worldwide", "Europe", etc.) feeds the existing evaluate() eligibility logic, which must NOT mark Türkiye eligibility VERIFIED (global_unconfirmed/unknown only). Rate-limit politely (identifying User-Agent, single request per feed per scan, no retries storm), handle large responses and malformed items per-item without failing the scan, report feed errors in the existing "Kaynak hataları" section.
6. Hard exclusions in CLAUDE.md/profile.yaml and applied jobs stay enforced for aggregator jobs.
7. Tests with recorded fixtures (no live network in tests) for each feed parser, dedupe, attribution in report, board-URL extraction, and error handling. Keep all 70 existing tests passing (`python -m pytest -q`).

Out of scope: LinkedIn or any authenticated/protected site; auto-applying; sending email to recruiters; scheduled/daily automation (separate later feature).

Note for implementer: working tree has uncommitted changes from the previous feature (harden-scan-pipeline); the state helper rejects a dirty source tree at `start`, so the user must commit first.
