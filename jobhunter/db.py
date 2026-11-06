import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
 company TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
 location TEXT, description TEXT, published_at TEXT, date_kind TEXT,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1,
 eligibility_status TEXT NOT NULL DEFAULT 'unknown', eligibility_evidence TEXT,
 score INTEGER NOT NULL DEFAULT 0, technologies_json TEXT NOT NULL DEFAULT '[]',
 reviewed_status TEXT, reviewed_at TEXT, reported_at TEXT, reported_via TEXT,
 employment_type TEXT, salary TEXT,
 UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(eligibility_status,score);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), company TEXT NOT NULL,
 title TEXT NOT NULL, url TEXT, status TEXT NOT NULL CHECK(status IN ('applied','rejected','interview','offer','withdrawn')),
 applied_at TEXT NOT NULL, notes TEXT,
 UNIQUE(company,title,url)
);
CREATE TABLE IF NOT EXISTS scan_runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 found_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0,
 errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS review_events (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 reviewed_at TEXT NOT NULL, reviewer TEXT NOT NULL, status TEXT NOT NULL,
 evidence_url TEXT, evidence_quote TEXT, notes TEXT
);
'''

def now():
    return datetime.now(timezone.utc).isoformat()

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn

HEURISTIC_STATUSES = {'unknown','global_unconfirmed','candidate_tr','excluded','irrelevant','ineligible'}
REVIEW_STATUSES = ('VERIFIED_TR','GLOBAL_UNCONFIRMED','UNKNOWN','INELIGIBLE')
REVIEW_ALIASES = {'REMOTE_WORLDWIDE_UNCONFIRMED':'GLOBAL_UNCONFIRMED'}
NEW_COLUMNS = {'reviewed_status':'TEXT','reviewed_at':'TEXT','reported_at':'TEXT','reported_via':'TEXT',
               'employment_type':'TEXT','salary':'TEXT'}

def migrate(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(jobs)')}
    for name, typ in NEW_COLUMNS.items():
        if name not in cols:
            conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {typ}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_reported ON jobs(reported_at)')
    if conn.execute('PRAGMA user_version').fetchone()[0] < 1:
        t = now()
        # Jobs that predate reporting markers were covered by the old overwrite-style reports.
        conn.execute("UPDATE jobs SET reported_via='legacy', reported_at=first_seen_at WHERE reported_at IS NULL")
        rows = conn.execute('SELECT id, eligibility_status FROM jobs WHERE reviewed_status IS NULL').fetchall()
        for r in rows:
            st = r['eligibility_status']
            if st and st.lower() not in HEURISTIC_STATUSES:
                st = st.upper()  # normalise e.g. 'Verified_TR' so the legacy label logic recognises it
                conn.execute('UPDATE jobs SET reviewed_status=?, reviewed_at=? WHERE id=?', (st, t, r['id']))
                conn.execute('''INSERT INTO review_events(job_id,reviewed_at,reviewer,status,notes) VALUES(?,?,?,?,?)''',
                             (r['id'], t, 'legacy-migration', st, 'structured evidence missing; re-verify'))
        conn.execute('PRAGMA user_version = 1')
    conn.commit()

def effective_status(row):
    """Reviewed status wins over the heuristic one, except heuristic 'excluded' which is final."""
    heur = row['eligibility_status']
    if heur == 'excluded':
        return 'excluded'
    return row['reviewed_status'] or heur

def record_review(conn, job_id, status, reviewer, evidence_url=None, evidence_quote=None, notes=None):
    status = (status or '').strip().upper()
    status = REVIEW_ALIASES.get(status, status)
    if status not in REVIEW_STATUSES:
        raise ValueError('Invalid status; allowed: ' + ', '.join(REVIEW_STATUSES))
    if not (reviewer or '').strip():
        raise ValueError('reviewer is required')
    url = (evidence_url or '').strip()
    quote = (evidence_quote or '').strip()
    if status == 'VERIFIED_TR':
        if not url.lower().startswith('https://') or len(url) <= len('https://'):
            raise ValueError('VERIFIED_TR requires an https --evidence-url')
        if not quote:
            raise ValueError('VERIFIED_TR requires a non-empty --evidence-quote')
    if conn.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone() is None:
        raise ValueError(f'Job not found: {job_id}')
    t = now()
    with conn:
        conn.execute('INSERT INTO review_events(job_id,reviewed_at,reviewer,status,evidence_url,evidence_quote,notes) VALUES(?,?,?,?,?,?,?)',
                     (job_id, t, reviewer.strip(), status, url or None, quote or None, notes or None))
        conn.execute('UPDATE jobs SET reviewed_status=?, reviewed_at=? WHERE id=?', (status, t, job_id))
    return status

def latest_review(conn, job_id):
    r = conn.execute('SELECT * FROM review_events WHERE job_id=? ORDER BY id DESC LIMIT 1', (job_id,)).fetchone()
    return dict(r) if r else None

def unreported_jobs(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM jobs WHERE reported_at IS NULL AND eligibility_status NOT IN ('excluded','irrelevant') ORDER BY score DESC, id")]

def mark_reported(conn, ids, via, at=None):
    at = at or now()
    with conn:
        conn.executemany('UPDATE jobs SET reported_at=?, reported_via=? WHERE id=? AND reported_at IS NULL',
                         [(at, via, i) for i in ids])

def all_jobs(conn):
    return [dict(r) for r in conn.execute('SELECT * FROM jobs ORDER BY id')]

def update_heuristics(conn, job_id, ev):
    """Update ONLY the heuristic fields; reviewed_*/reported_*/last_seen_at are never touched."""
    conn.execute('UPDATE jobs SET eligibility_status=?, eligibility_evidence=?, score=?, technologies_json=? WHERE id=?',
                 (ev['status'], ev['evidence'], ev['score'], json.dumps(ev['tech'], ensure_ascii=False), job_id))

def is_applied(conn, job):
    return conn.execute('''SELECT 1 FROM applications WHERE lower(company)=lower(?)
        AND (url=? OR lower(title)=lower(?)) LIMIT 1''',
        (job['company'],job['url'],job['title'])).fetchone() is not None

def _text_field(value):
    """Source-provided text column: lists are joined, empty or non-text values become None."""
    if isinstance(value, (list, tuple)):
        value = ', '.join(str(v) for v in value if isinstance(v, str) and v.strip())
    return value.strip() if isinstance(value, str) and value.strip() else None

def upsert(conn, job, ev):
    existing = conn.execute('SELECT id FROM jobs WHERE source=? AND source_id=?', (job['source'],job['source_id'])).fetchone()
    t = now()
    conn.execute('''INSERT INTO jobs (source,source_id,company,title,url,location,description,published_at,date_kind,first_seen_at,last_seen_at,eligibility_status,eligibility_evidence,score,technologies_json,employment_type,salary)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source,source_id) DO UPDATE SET company=excluded.company,title=excluded.title,url=excluded.url,
        location=excluded.location,description=excluded.description,published_at=excluded.published_at,date_kind=excluded.date_kind,
        last_seen_at=excluded.last_seen_at,active=1,eligibility_status=excluded.eligibility_status,
        eligibility_evidence=excluded.eligibility_evidence,score=excluded.score,technologies_json=excluded.technologies_json,
        employment_type=excluded.employment_type,salary=excluded.salary''',
        (job['source'],job['source_id'],job['company'],job['title'],job['url'],job.get('location'),job.get('description'),job.get('published_at'),job.get('date_kind'),t,t,ev['status'],ev['evidence'],ev['score'],json.dumps(ev['tech'],ensure_ascii=False),_text_field(job.get('employment_type')),_text_field(job.get('salary'))))
    return existing is None
