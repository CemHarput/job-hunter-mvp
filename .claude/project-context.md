# Project context

This file is project-specific guidance, not a Claude Code settings schema.

- Stack: Python >=3.11, httpx, PyYAML, BeautifulSoup, SQLite (`pyproject.toml`).
- Architecture: `jobhunter/` package: `collectors.py` (public ATS feeds), `evaluate.py`
  (heuristics), `db.py` (SQLite), `cli.py` (scan, mark-applied, list). Agents in
  `.claude/agents/` for job research; deterministic code owns DB writes.
- Test command: `python -m pytest -q` (install with `pip install -e ".[test]"`).
- Runtime: SMTP settings via environment variables (see `.env.example`).
- Domain constraints: read `CLAUDE.md` (job-hunter contract). Never invent jobs,
  never apply or email recruiters without explicit approval, honor hard exclusions.
- Note: the working directory is not a Git repository yet; `/feature-review` and
  `/feature-fast` need `git init` first.

Do not invent a command, silently install dependencies, or report unrun checks
as passing.
