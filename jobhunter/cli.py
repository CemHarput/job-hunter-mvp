import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import yaml
from .collectors import collect
from .db import (all_jobs, connect, effective_status, is_applied, latest_review, mark_reported, now,
                 record_review, unreported_jobs, update_heuristics, upsert)
from .evaluate import SEARCH_REJECT_PREFIX, evaluate, search_config, validate_profile, validate_search
from .locations import LOC_RESTRICTED_PREFIX, classify_location, load_locations

ROOT = Path(__file__).resolve().parent.parent

def config(path):
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

def load_profile(path):
    """Load the profile; merge company lists from an optional gitignored `<name>.local.yaml` sibling
    (e.g. config/profile.local.yaml) so personal exclusion/application history stays out of the repo."""
    profile = config(path)
    p = Path(path)
    local = p.with_name(p.stem + '.local' + p.suffix)
    if local.is_file():
        extra = config(local)
        for key in ('exclude_companies', 'low_priority_companies'):
            merged = list(profile.get(key) or [])
            merged += [c for c in (extra.get(key) or []) if c not in merged]
            if merged:
                profile[key] = merged
    return profile

def load_env(path=None):
    """Load KEY=VALUE lines from .env without overriding existing environment variables."""
    path = Path(path) if path else ROOT / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():  # utf-8-sig: tolerate a Windows BOM
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value

def parse_published(value):
    """Parse an ISO-8601 timestamp to an aware datetime; None when missing or unparseable."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def _review_text(j):
    """Describe a recorded review (reviewer, evidence URL, quote); legacy rows without evidence are flagged."""
    rv = j.get('review')
    if not rv:
        return ''
    if rv.get('evidence_url') and rv.get('evidence_quote'):
        quote = ' '.join(rv['evidence_quote'].split())  # keep the Markdown report on one line
        return f'inceleme ({rv["reviewer"]}): kanıt {rv["evidence_url"]} — "{quote}"'
    if rv['status'] == 'VERIFIED_TR':
        return f'inceleme ({rv["reviewer"]}): legacy, kanıt yok, yeniden doğrula'
    return f'inceleme ({rv["reviewer"]})' + (f': {rv["notes"]}' if rv.get('notes') else '')

def _eligibility_text(j):
    heuristic = j['eligibility_evidence'] or ''
    review = _review_text(j) if effective_status(j) != 'excluded' else ''
    return f'{review} | sezgisel: {heuristic}' if review else heuristic

def _job_lines(j):
    status = effective_status(j)
    evidence = _eligibility_text(j)
    label = ' (Düşük öncelik)' if 'Düşük öncelik' in evidence else ''
    return [f'## {j["company"]} — {j["title"]}{label}',
            f'- Başvuru: {j["url"]}', f'- Konum: {j.get("location") or "Belirtilmemiş"}',
            f'- İlan tarihi ({j.get("date_kind") or "bilinmiyor"}): {j.get("published_at") or "Belirtilmemiş"}',
            f'- Uygunluk: **{status}** — {evidence}',
            f'- Teknolojiler: {", ".join(json.loads(j["technologies_json"])) or "Tespit edilemedi"}',
            f'- Teknik skor (heuristic): {j["score"]}/100',
            '- Sözleşme süresi: Doğrulanmadı',
            '- Ücret: Doğrulanmadı (orijinal ilanı kontrol edin)', '']

def _loc_group(j):
    """Label of a location-based rejection ("United States"), or None for any other rejection."""
    ev = j.get('eligibility_evidence') or ''
    if j.get('reviewed_status') or not ev.startswith(LOC_RESTRICTED_PREFIX) or ']' not in ev:
        return None
    return ev[len(LOC_RESTRICTED_PREFIX):ev.index(']')]

def _search_group(j):
    """Label of a search-profile rejection ("EMEA dışı: United States"), or None."""
    ev = j.get('eligibility_evidence') or ''
    if j.get('reviewed_status') or not ev.startswith(SEARCH_REJECT_PREFIX) or ']' not in ev:
        return None
    return ev[len(SEARCH_REJECT_PREFIX):ev.index(']')]

def _group(j):
    """Rejection group label (location or search-profile), or None for any other rejection."""
    g = _loc_group(j)
    return g if g is not None else _search_group(j)

def _one_line(value):
    return ' '.join(str(value or '').split())

def _cell(value):
    return ' '.join(str(value if value not in (None, '') else '—').replace('|', '/').split())

SEARCH_TITLE = 'EMEA · Remote · B2B/Contract'
SEARCH_HEADER = ['| Şirket | Pozisyon | Konum | Çalışma şekli (kanıt) | Ücret | Kaynak | İlk görülme | Uygunluk notu | Skor |',
                 '|---|---|---|---|---|---|---|---|---|']
SEARCH_EMPTY = ('Bu taramada arama profilini geçen raporlanmamış ve başvurulmamış ilan bulunmadı. '
                'Not: ATS panoları B2B/contract ifadesini nadiren belirtir; `job-discovery-sources` '
                '(aggregator kaynakları) gelene kadar liste büyük olasılıkla boş veya çok kısa kalır.')

def _search_row(j, d):
    d = d or {}
    status = effective_status(j)
    note = d.get('note') or f'sezgisel ret: {j.get("eligibility_evidence") or "—"} (inceleme ile aday)'
    if j.get('review'):
        note += f' | {_review_text(j)}'
    return '| ' + ' | '.join(_cell(x) for x in [
        j['company'], j['title'], j.get('location') or 'Belirtilmemiş',
        d.get('engagement') or 'Belirtilmemiş', d.get('pay') or 'Doğrulanmadı',
        f'{j.get("source_name") or j["source"]} — {j.get("apply_url") or j["url"]}',
        (j.get('first_seen_at') or '')[:10] or '—', f'{note} [{status}]', j['score']]) + ' |'

SEARCH_TITLE_NO_ENGAGEMENT = 'EMEA · Remote'
SEARCH_EMPTY_NO_ENGAGEMENT = 'Bu taramada arama profilini (EMEA, remote) geçen raporlanmamış ve başvurulmamış ilan bulunmadı.'

def _search_table(rows, engagement_required=True):
    """Markdown sections for (row, search details) pairs: main table, then the Türkiye/reviewed one."""
    main = [(j, d) for j, d in rows if (d or {}).get('section') != 'turkiye' and j.get('reviewed_status') != 'VERIFIED_TR']
    tr = [(j, d) for j, d in rows if (d or {}).get('section') == 'turkiye' or j.get('reviewed_status') == 'VERIFIED_TR']
    title = SEARCH_TITLE if engagement_required else SEARCH_TITLE_NO_ENGAGEMENT
    lines = [f'## {title} ({len(main)})', '']
    if main:
        lines += SEARCH_HEADER + [_search_row(j, d) for j, d in main]
    else:
        lines.append(SEARCH_EMPTY if engagement_required else SEARCH_EMPTY_NO_ENGAGEMENT)
    lines.append('')
    if tr:
        lines += [f'## Türkiye · Remote / elle incelenmiş ({len(tr)})', ''] + SEARCH_HEADER + [_search_row(j, d) for j, d in tr] + ['']
    return lines

def rejected_text(rejected):
    lines = ['# Elenen ilanların tam listesi', '', '#id | company — title | konum | url | kanıt', '']
    for j in rejected:
        lines.append(f'#{j["id"]} | {j["company"]} — {j["title"]} | {_one_line(j.get("location")) or "Belirtilmemiş"} | {j["url"]} | {_one_line(_eligibility_text(j))}')
    return '\n'.join(lines) + '\n'

def report(jobs, errors, count, new_count, rejected=None, baseline_count=0, old_count=0,
           rejected_path=None, unclassified=None, search=None, engagement_required=True):
    """search=None: classic per-job blocks. search=[(row, details)]: search-profile tables instead."""
    rejected = rejected or []
    loc_rejected = sum(1 for j in rejected if _loc_group(j) is not None)
    search_rejected = sum(1 for j in rejected if _search_group(j) is not None)
    detail = f'konum: {loc_rejected}' + (f', arama profili: {search_rejected}' if search is not None and search_rejected else '')
    lines = [f'# Job Hunter — {datetime.now().strftime("%Y-%m-%d %H:%M")}',
             '', f'Taranan: {count} | İlk kez görülen: {new_count} | Rapora uygun aday: {len(jobs)} | Elenen: {len(rejected)} ({detail})',
             '', '**Not:** Bu ilanlar otomatik olarak Türkiye uygunluğu doğrulanmış sayılmaz.', '']
    if baseline_count:
        lines += [f'Baseline: {baseline_count} ilk kez görülen ilan listelenmeden raporlanmış olarak işaretlendi.', '']
    if old_count:
        lines += [f'Yaş filtresi: {old_count} eski ilan listelenmedi (işaretlenmedi).', '']
    if search is not None:
        lines += _search_table(search, engagement_required)
    else:
        for j in jobs:
            lines += _job_lines(j)
        if not jobs:
            lines.append('Bu taramada filtreleri geçen raporlanmamış ve başvurulmamış ilan bulunmadı.\n')
    if rejected:
        lines += ['## Elenen ilanlar (ineligible)', '']
        groups = {}
        for j in rejected:
            g = _group(j)
            if g is None:
                lines.append(f'- {j["company"]} — {j["title"]} | {j["url"]} | {_eligibility_text(j)}')
            else:
                groups[g] = groups.get(g, 0) + 1
        for g, n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f'- {g}: {n} ilan')
        if rejected_path:
            lines.append(f'- Tam liste (iş başına kanıt): {rejected_path} | `python -m jobhunter list --rejected`')
        lines.append('')
    if unclassified:
        lines += ['## Sınıflandırılamayan konum ifadeleri', '']
        lines += [f'- "{loc}" ({n})' for loc, n in unclassified[:20]]
        lines += ['', 'Tabloyu genişletmek için: `python -m jobhunter locations --unclassified` (config/locations.yaml)', '']
    if errors:
        lines += ['## Kaynak hataları', *[f'- {e}' for e in errors]]
    return '\n'.join(lines) + '\n'

def send_email(subject, body):
    needed = ['SMTP_HOST','SMTP_FROM','REPORT_TO']
    if os.getenv('SMTP_USER'):
        needed.append('SMTP_PASSWORD')
    missing = [k for k in needed if not os.getenv(k)]
    if missing:
        raise ValueError('SMTP configuration missing: ' + ', '.join(missing))
    try:
        port = int(os.getenv('SMTP_PORT','587'))
    except ValueError:
        raise ValueError(f'Invalid SMTP_PORT: {os.getenv("SMTP_PORT")!r} (must be an integer)') from None
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, os.environ['SMTP_FROM'], os.environ['REPORT_TO']
    msg.set_content(body)
    host = os.environ['SMTP_HOST']
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if os.getenv('SMTP_STARTTLS','true').lower() == 'true':
            smtp.starttls(context=ssl.create_default_context())
        if os.getenv('SMTP_USER'):
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)

def load_table(path=None):
    """Load the location table; a missing file gives the built-in table (no country/city elimination) plus a warning."""
    path = Path(path) if path else ROOT / 'config/locations.yaml'
    table, missing = load_locations(path)
    if missing:
        print(f'Uyarı: {path} yok; ülke/şehir elemesi kapalı (Hybrid/On-site elemesi yine çalışır)', file=sys.stderr)
    return table

def run_scan(args):
    load_env(getattr(args, 'env_file', None))
    profile, sources = load_profile(args.profile), config(args.sources)
    try:
        for w in validate_profile(profile):
            print(f'Uyarı: {w}', file=sys.stderr)
    except ValueError as e:
        print(f'Profile error: {e}', file=sys.stderr)
        return 1
    try:
        table = load_table(getattr(args, 'locations', None))
    except ValueError as e:
        print(f'Locations error: {e}', file=sys.stderr)
        return 1
    try:
        validate_search(profile, table)
    except ValueError as e:
        print(f'Profile error: {e}', file=sys.stderr)
        return 1
    max_age = getattr(args, 'max_age_days', None)
    if max_age is None:
        max_age = profile.get('max_posting_age_days')
    conn = connect(args.db)
    started = now()
    jobs, errors = collect(sources)
    first_ids = []
    with conn:
        for j in jobs:
            ev = evaluate(j, profile, table)
            if upsert(conn, j, ev):
                first_ids.append(conn.execute('SELECT id FROM jobs WHERE source=? AND source_id=?', (j['source'], j['source_id'])).fetchone()['id'])
        conn.execute('INSERT INTO scan_runs(started_at,finished_at,found_count,new_count,errors_json) VALUES(?,?,?,?,?)',
                     (started,now(),len(jobs),len(first_ids),json.dumps(errors)))
    baseline_count = 0
    if getattr(args, 'baseline', False):
        mark_reported(conn, first_ids, 'baseline')
        baseline_count = len(first_ids)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age is not None else None
    candidates, rejected, old_count = [], [], 0
    for row in unreported_jobs(conn):
        if is_applied(conn, row):
            continue
        status = effective_status(row)
        if status == 'excluded' or status.lower() == 'irrelevant':
            continue
        if row['reviewed_status']:
            row['review'] = latest_review(conn, row['id'])
        pub = parse_published(row['published_at'])
        if cutoff is not None and pub is not None and pub < cutoff:
            old_count += 1
            continue
        (rejected if status.lower() == 'ineligible' else candidates).append(row)
    candidates.sort(key=lambda j: j['score'], reverse=True)
    unclassified = {}
    for j in candidates:
        loc = j.get('location') or ''
        if classify_location(loc, table).unclassified:
            unclassified[loc] = unclassified.get(loc, 0) + 1
    unclassified = sorted(unclassified.items(), key=lambda kv: (-kv[1], kv[0]))
    dest = Path(args.output)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = dest.parent / 'archive' / f'scan-{stamp}.md'
    rejected_path = archive.parent / f'scan-{stamp}-rejected.md' if rejected else None
    search = [(j, evaluate(j, profile, table).get('search')) for j in candidates] if 'search' in profile else None
    text = report(candidates, errors, len(jobs), len(first_ids), rejected, baseline_count, old_count,
                  rejected_path, unclassified, search=search,
                  engagement_required=bool(search_config(profile)['engagement']) if 'search' in profile else True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(text, encoding='utf-8')
    if rejected_path:
        rejected_path.write_text(rejected_text(rejected), encoding='utf-8')
    print(f'Report: {dest} | archive={archive} | scanned={len(jobs)} new={len(first_ids)} listed={len(candidates)} rejected={len(rejected)} baseline={baseline_count} errors={len(errors)}')
    via = 'report'
    if args.email:
        try:
            send_email(f'Job Hunter | {len(candidates)} ilan', text)
        except Exception as e:  # keep jobs unreported so the next scan lists them again
            print(f'E-posta gönderilemedi: {e}. İlanlar raporlanmış sayılmadı.', file=sys.stderr)
            conn.close()
            return 1
        print('E-posta gönderildi.')
        via = 'email'
    mark_reported(conn, [j['id'] for j in candidates + rejected], via)
    conn.close()
    return 0

def review(args):
    conn = connect(args.db)
    try:
        if args.job_id is not None:
            rows = conn.execute('SELECT id FROM jobs WHERE id=?', (args.job_id,)).fetchall()
        else:
            rows = conn.execute('SELECT id FROM jobs WHERE url=?', (args.url,)).fetchall()
        if not rows:
            print('Job not found.', file=sys.stderr)
            return 1
        if len(rows) > 1:
            print('URL matches multiple jobs; use --job-id.', file=sys.stderr)
            return 1
        try:
            status = record_review(conn, rows[0]['id'], args.status, args.reviewer,
                                   args.evidence_url, args.evidence_quote, args.notes)
        except ValueError as e:
            print(f'Review rejected: {e}', file=sys.stderr)
            return 1
        print(f'Review recorded: job {rows[0]["id"]} -> {status}')
        return 0
    finally:
        conn.close()

def mark_applied(args):
    conn = connect(args.db)
    with conn:
        row = conn.execute('SELECT id FROM jobs WHERE url=?', (args.url,)).fetchone() if args.url else None
        conn.execute('''INSERT INTO applications(job_id,company,title,url,status,applied_at,notes)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(company,title,url) DO UPDATE SET status=excluded.status,notes=excluded.notes''',
            (row['id'] if row else None,args.company,args.title,args.url or '',args.status,now(),args.notes or ''))
    print('Başvuru kaydedildi.')
    conn.close()

def list_rejected(conn, limit):
    shown = 0
    for r in conn.execute('SELECT * FROM jobs ORDER BY id').fetchall():
        r = dict(r)
        if effective_status(r).lower() != 'ineligible':  # reviewed status is upper-case (INELIGIBLE)
            continue
        if r['reviewed_status']:
            r['review'] = latest_review(conn, r['id'])
        print(f'{r["id"]} | {r["reported_via"] or "raporlanmadı"} | {r["company"]} — {r["title"]} | '
              f'{_one_line(r["location"]) or "Belirtilmemiş"} | {r["url"]} | {_one_line(_eligibility_text(r))}')
        shown += 1
        if limit and shown >= limit:
            break

def list_jobs(args):
    conn = connect(args.db)
    if getattr(args, 'rejected', False):
        list_rejected(conn, args.limit)
        conn.close()
        return 0
    shown = 0
    for r in conn.execute('SELECT * FROM jobs ORDER BY score DESC'):
        st = effective_status(r)
        if st.lower() in ('excluded','irrelevant','ineligible') or is_applied(conn, r):
            continue  # applied jobs (mark-applied) are not shown again
        rv = latest_review(conn, r['id']) if r['reviewed_status'] else None
        if st == 'VERIFIED_TR' and rv and not rv.get('evidence_url'):
            st = 'VERIFIED_TR (kanıt yok)'
        print(f'{r["score"]:3d} | {st:19s} | {r["company"]} — {r["title"]} | {r["url"]}')
        shown += 1
        if shown >= args.limit:
            break
    conn.close()

def reevaluate(args):
    """Re-run the heuristics on stored jobs (no network). Only the heuristic fields are updated."""
    profile = load_profile(args.profile)
    try:
        for w in validate_profile(profile):
            print(f'Uyarı: {w}', file=sys.stderr)
    except ValueError as e:
        print(f'Profile error: {e}', file=sys.stderr)
        return 1
    try:
        table = load_table(getattr(args, 'locations', None))
    except ValueError as e:
        print(f'Locations error: {e}', file=sys.stderr)
        return 1
    try:
        validate_search(profile, table)
    except ValueError as e:
        print(f'Profile error: {e}', file=sys.stderr)
        return 1
    dry = getattr(args, 'dry_run', False)
    conn = connect(args.db)
    rows = all_jobs(conn)
    moves, newly_rejected, updates = {}, 0, []
    searching = 'search' in profile
    passed, listable, rejects = 0, 0, {}
    for r in rows:
        ev = evaluate(r, profile, table)
        if searching:
            if ev['status'] not in ('excluded', 'irrelevant', 'ineligible'):
                passed += 1
                if not r.get('reported_at') and not is_applied(conn, r):
                    listable += 1
            elif ev['evidence'].startswith(SEARCH_REJECT_PREFIX) and ']' in ev['evidence']:
                g = ev['evidence'][len(SEARCH_REJECT_PREFIX):ev['evidence'].index(']')]
                rejects[g] = rejects.get(g, 0) + 1
        tech = json.dumps(ev['tech'], ensure_ascii=False)
        if (ev['status'], ev['evidence'], ev['score'], tech) == (r['eligibility_status'], r['eligibility_evidence'], r['score'], r['technologies_json']):
            continue
        updates.append((r['id'], ev))
        if ev['status'] != r['eligibility_status']:
            key = f'{r["eligibility_status"]} -> {ev["status"]}'
            moves[key] = moves.get(key, 0) + 1
            # reviewed jobs take their effective status from the review, so they are not "newly rejected"
            if ev['status'] == 'ineligible' and r['reported_at'] and not r['reviewed_status']:
                newly_rejected += 1
    if not dry:
        with conn:
            for job_id, ev in updates:
                update_heuristics(conn, job_id, ev)
    conn.close()
    print(f'{"[dry-run] " if dry else ""}Yeniden değerlendirme: değişen {len(updates)} / {len(rows)}')
    for key, n in sorted(moves.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f'  {key}: {n}')
    if searching:
        print(f'Arama profili: geçen {passed} / {len(rows)} (raporlanmamış+başvurulmamış: {listable})')
        for g, n in sorted(rejects.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
            print(f'  red: {g}: {n}')
    print(f'Daha önce raporlanmış olup artık ineligible: {newly_rejected} (list --rejected)')
    return 0

def locations_cmd(args):
    try:
        table = load_table(getattr(args, 'locations', None))
    except ValueError as e:
        print(f'Locations error: {e}', file=sys.stderr)
        return 1
    conn = connect(args.db)
    counts = {}
    for r in conn.execute("SELECT location FROM jobs WHERE eligibility_status NOT IN ('excluded','irrelevant')"):
        loc = r['location'] or ''
        counts[loc] = counts.get(loc, 0) + 1
    conn.close()
    rows = []
    for loc, n in counts.items():
        res = classify_location(loc, table)
        if args.unclassified and not res.unclassified:
            continue
        rows.append((n, loc, res))
    rows.sort(key=lambda x: (-x[0], x[1]))
    if args.limit:
        rows = rows[:args.limit]
    print('adet | sınıf | etiket | residue | konum')
    for n, loc, res in rows:
        print(f'{n} | {res.outcome} | {", ".join(dict.fromkeys(res.labels or res.matched))} | {", ".join(res.unclassified)} | {_one_line(loc) or "(boş)"}')
    return 0

def main():
    # Turkish output (ğ, ş, ı, İ) crashes on Windows consoles using cp1252; force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description='Local job hunter MVP')
    p.add_argument('--db',default=str(ROOT/'data/jobs.sqlite3'))
    sub = p.add_subparsers(dest='cmd',required=True)
    scan = sub.add_parser('scan')
    scan.add_argument('--profile',default=str(ROOT/'config/profile.yaml'))
    scan.add_argument('--sources',default=str(ROOT/'config/sources.yaml'))
    scan.add_argument('--output',default=str(ROOT/'reports/latest.md'))
    scan.add_argument('--env-file',default=None,help='Path to .env (default: repo root .env)')
    scan.add_argument('--baseline',action='store_true',help='Mark jobs first seen in this scan as reported without listing them')
    scan.add_argument('--max-age-days',type=int,default=None,help='Do not list jobs published more than N days ago')
    scan.add_argument('--locations',default=None,help='Location table (default: config/locations.yaml)')
    scan.add_argument('--email',action='store_true',help='Send via configured SMTP; never default')
    rev = sub.add_parser('review', help='Record an eligibility review with evidence')
    target = rev.add_mutually_exclusive_group(required=True)
    target.add_argument('--job-id',type=int)
    target.add_argument('--url')
    rev.add_argument('--status',required=True)
    rev.add_argument('--reviewer',required=True)
    rev.add_argument('--evidence-url',default=None)
    rev.add_argument('--evidence-quote',default=None)
    rev.add_argument('--notes',default=None)
    apply = sub.add_parser('mark-applied')
    apply.add_argument('--company',required=True)
    apply.add_argument('--title',required=True)
    apply.add_argument('--url',default='')
    apply.add_argument('--status',choices=['applied','rejected','interview','offer','withdrawn'],default='applied')
    apply.add_argument('--notes',default='')
    listing = sub.add_parser('list')
    listing.add_argument('--limit',type=int,default=30,help='0 = unlimited (with --rejected)')
    listing.add_argument('--rejected',action='store_true',help='Show ineligible jobs (reported or not) with evidence')
    reeval = sub.add_parser('reevaluate', help='Re-run heuristics on stored jobs (offline; heuristic fields only)')
    reeval.add_argument('--profile',default=str(ROOT/'config/profile.yaml'))
    reeval.add_argument('--locations',default=None)
    reeval.add_argument('--dry-run',action='store_true')
    locs = sub.add_parser('locations', help='Show how stored location strings are classified')
    locs.add_argument('--unclassified',action='store_true')
    locs.add_argument('--limit',type=int,default=50,help='0 = unlimited')
    locs.add_argument('--locations',default=None)
    args = p.parse_args()
    rc = {'scan':run_scan,'review':review,'mark-applied':mark_applied,'list':list_jobs,
          'reevaluate':reevaluate,'locations':locations_cmd}[args.cmd](args)
    sys.exit(rc or 0)

if __name__ == '__main__':
    main()
