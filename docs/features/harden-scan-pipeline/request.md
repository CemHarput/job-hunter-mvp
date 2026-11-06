# Request: harden-scan-pipeline

Fix the important issues found in the code review of job-hunter-mvp:

1. Re-scans must not overwrite reviewed/verified eligibility. `db.upsert` currently
   overwrites eligibility_status/evidence/score on every scan, so a VERIFIED_TR set by
   eligibility-checker is lost. `review_events` has no writer and there is no CLI
   command to record an agent/human review with evidence URL and quote.
2. Report loss. `reports/latest.md` is overwritten each scan; first-seen jobs vanish
   from reports on a second scan or if email sending fails. Jobs need a reported
   marker/archive so unreported new jobs are never silently dropped.
3. First-scan flood. First scan treats every matching posting as new. Add a baseline
   mode and/or an age filter using published_at.
4. Loose regexes in evaluate.py: `java` matches `javascript`; `rag`, `aws` etc. match
   inside other words; `software engineer` lets frontend/mobile roles through.
5. Negative-eligibility filter: add missing patterns (US-based, authorized to work in
   the US, EU only) and avoid false positives like "join us only", because
   `ineligible` silently hides jobs.
6. Unused profile settings: `minimum_contract_months`, `low_priority_companies`
   (Supabase), `exclude_company_policy` must be applied or removed.
7. `.env` is not loaded; missing SMTP_PASSWORD gives a raw KeyError.

Constraints from CLAUDE.md: never invent jobs/dates/eligibility; candidate_tr is never
VERIFIED_TR without primary-source quote and URL; excluded companies stay excluded;
no auto-apply or auto-email. Keep tests passing and add tests for changed behavior.
