import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from jobhunter import cli
from jobhunter.db import connect, update_heuristics, upsert, record_review
from jobhunter.evaluate import evaluate
from jobhunter.locations import (BUILTIN_LOCATIONS, LOC_RESTRICTED_PREFIX, classify_location, load_locations)

ROOT = Path(__file__).resolve().parent.parent
TABLE, MISSING = load_locations(ROOT / 'config/locations.yaml')
PROFILE = {'exclude_companies': ['Insider One'], 'technologies': {'strong': ['python', 'fastapi']},
           'required': {'minimum_contract_months': 6}, 'low_priority_companies': ['Supabase']}


def job(**kw):
    j = dict(source='ashby', source_id='1', company='ExampleCo', title='Senior Python Backend Engineer',
             url='https://jobs.example.com/1', location='Remote', description='Python FastAPI',
             published_at=None, date_kind=None)
    j.update(kw)
    return j


def ev(loc, **kw):
    return evaluate(job(location=loc, **kw), PROFILE, TABLE)


INELIGIBLE = [
    'United States', 'United States (Remote)', 'Canada (Remote)', 'Spain', 'Greece', 'Ireland', 'United Kingdom',
    'Portugal', 'Poland', 'Romania', 'Norway', 'Switzerland', 'Canada', 'Bangalore, India', 'Gurugram',
    'London, UK', 'Sofia, Bulgaria', 'Berlin, Germany', 'Dublin', 'Toronto', 'Kyiv', 'The Netherlands',
    'Americas', 'NAMER and LatAm', 'Remote, United Kingdom', 'Remote, Canada; Remote, United States',
    'Remote, Canada; Remote, Poland; Remote, United Kingdom', 'San Francisco, CA | New York City, NY',
    'San Francisco, CA | New York City, NY | Seattle, WA', 'Hybrid', 'In-Office', 'Spain or Portugal',
    'Remote, EU', 'Istanbul, Türkiye', 'Hybrid - Istanbul',
]
OPEN = ['Home based - Worldwide', 'Remote, Global', 'Remote, Poland | Worldwide']
REGIONAL = ['Home based - EMEA', 'Europe', 'Home Based - Americas; Home based - EMEA', 'EMEA (Remote) | United States']
UNKNOWN = ['Remote', '', 'Remote-Friendly (Travel-Required) | San Francisco, CA', 'Berlin | Remote', 'Remote / Hybrid',
           'Remote-Friendly (Travel-Required) | San Francisco, CA | …', 'Türkiye']
TURKIYE = ['Remote, Turkey', 'Remote - Istanbul', 'Ankara']


def test_starter_table_loads():
    assert not MISSING


@pytest.mark.parametrize('loc', INELIGIBLE)
def test_restricted(loc):
    r = ev(loc)
    assert r['status'] == 'ineligible' and r['evidence'].startswith(LOC_RESTRICTED_PREFIX) and r['score'] == 0
    assert f'"{loc}"' in r['evidence']


@pytest.mark.parametrize('loc', OPEN)
def test_open(loc):
    r = ev(loc)
    assert r['status'] == 'global_unconfirmed' and r['evidence'].startswith('Konum açık')
    assert 'doğrulanmadı' in r['evidence']


@pytest.mark.parametrize('loc', REGIONAL)
def test_regional(loc):
    r = ev(loc)
    assert r['status'] == 'global_unconfirmed' and r['evidence'].startswith('Bölgesel (')
    assert 'Türkiye doğrulanmadı' in r['evidence']


@pytest.mark.parametrize('loc', UNKNOWN)
def test_unknown(loc):
    assert ev(loc)['status'] == 'unknown'


@pytest.mark.parametrize('loc', TURKIYE)
def test_turkiye_candidate_not_verified(loc):
    r = ev(loc)
    assert r['status'] == 'candidate_tr' and r['status'] != 'VERIFIED_TR'
    assert r['evidence'].startswith('Konum Türkiye + remote/Ankara')


def test_turkiye_country_only_text_logic_can_upgrade():
    assert ev('Türkiye', description='Python. Remote in Türkiye, location open')['status'] == 'candidate_tr'


@pytest.mark.parametrize('loc,res', [('Remote, Narnia', 'narnia'), ('Hybrid, Gotham', 'gotham')])
def test_residue_not_rejected(loc, res):
    c = classify_location(loc, TABLE)
    assert c.outcome == 'unclassified' and c.unclassified == [res]
    r = ev(loc)
    assert r['status'] == 'unknown' and f'Sınıflandırılamayan konum: "{res}"' in r['evidence']


def test_evidence_texts():
    assert ev('United States (Remote)')['evidence'].startswith('Location restricted [United States]: "United States (Remote)"')
    assert 'Bölgesel (EMEA), Türkiye doğrulanmadı' in ev('Home based - EMEA')['evidence']
    assert 'Hybrid' in ev('Hybrid')['evidence'] and '[On-site/hybrid]' in ev('Hybrid')['evidence']


def test_never_verified():
    for loc in INELIGIBLE + OPEN + REGIONAL + UNKNOWN + TURKIYE:
        assert ev(loc)['status'] != 'VERIFIED_TR'


def test_multi_value_any_open_restricted_only_if_all():
    assert classify_location('Remote, Canada | Worldwide', TABLE).outcome == 'open'
    assert classify_location('Spain; Portugal', TABLE).outcome == 'restricted'
    assert classify_location('Spain | Remote', TABLE).outcome == 'remote_bare'


def test_case_sensitive_abbreviations():
    assert classify_location('us', TABLE).outcome == 'unclassified'
    assert classify_location('US', TABLE).outcome == 'restricted'


def test_precedence_negative_beats_location_and_location_beats_contract():
    r = ev('Remote', description='Remote, must be US-based')
    assert r['evidence'].startswith('Explicit geographic')
    r = ev('United States', description='3-month contract role')
    assert r['evidence'].startswith(LOC_RESTRICTED_PREFIX)
    r = ev('Remote', description='3-month contract role')
    assert r['evidence'].startswith('Stated contract length')


def test_no_table_means_no_elimination():
    r = evaluate(job(location='United States'), PROFILE)
    assert r['status'] != 'ineligible'
    assert evaluate(job(location='Worldwide'), PROFILE, BUILTIN_LOCATIONS)['status'] == 'global_unconfirmed'


def test_excluded_and_irrelevant_win_over_location():
    assert evaluate(job(company='Insider One', location='Worldwide'), PROFILE, TABLE)['status'] == 'excluded'
    assert evaluate(job(title='Designer', location='United States'), PROFILE, TABLE)['status'] == 'irrelevant'


def test_score_order_and_range():
    s = {k: ev(l)['score'] for k, l in (('open', 'Worldwide'), ('regional', 'EMEA'), ('unknown', 'Remote'), ('tr', 'Remote, Turkey'))}
    assert s['tr'] > s['open'] > s['regional'] > s['unknown']
    assert all(0 <= v <= 100 for v in s.values())


# ---- table loading -------------------------------------------------------------------------

def _write(tmp_path, text):
    p = tmp_path / 'loc.yaml'
    p.write_text(text, encoding='utf-8')
    return p


def test_missing_file_builtin(tmp_path):
    t, missing = load_locations(tmp_path / 'nope.yaml')
    assert missing and t is BUILTIN_LOCATIONS


@pytest.mark.parametrize('text', [
    '- a\n', 'bogus: [a]\n', 'open: [a]\nregional: [A]\n', 'open: [1]\n', 'open: x\n',
    'restricted:\n  X: [foo]\n  Y: [Foo]\n', 'restricted: [a]\n', 'us_state_codes: [ca]\n', 'open: [a\n',
])
def test_invalid_table(tmp_path, text):
    with pytest.raises(ValueError):
        load_locations(_write(tmp_path, text))


def test_custom_table_editable(tmp_path):
    t, _ = load_locations(_write(tmp_path, 'restricted:\n  Narnia: [Cair Paravel]\n'))
    assert classify_location('Cair Paravel', t).outcome == 'restricted'


# ---- cli -----------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    jobs = []
    monkeypatch.setattr(cli, 'collect', lambda sources: (list(jobs), []))
    prof = tmp_path / 'profile.yaml'
    prof.write_text('exclude_companies: [Insider One]\n', encoding='utf-8')
    src = tmp_path / 'sources.yaml'
    src.write_text('{}', encoding='utf-8')

    def scan(**kw):
        a = dict(db=str(tmp_path / 'j.sqlite3'), profile=str(prof), sources=str(src),
                 output=str(tmp_path / 'reports' / 'latest.md'), email=False, env_file=str(tmp_path / 'none.env'),
                 baseline=False, max_age_days=None)
        a.update(kw)
        rc = cli.run_scan(Namespace(**a))
        return rc, (tmp_path / 'reports' / 'latest.md').read_text(encoding='utf-8') if (tmp_path / 'reports' / 'latest.md').exists() else ''

    return Namespace(jobs=jobs, scan=scan, tmp=tmp_path, db=str(tmp_path / 'j.sqlite3'), prof=str(prof))


@pytest.mark.parametrize('loc', ['Hybrid', 'On-site', 'In office', 'In-Office', 'Hybrid - EMEA'])
def test_onsite_hybrid_eliminated_even_without_table(loc):
    # Decision: the on-site/hybrid rule is code-level, so it applies without config/locations.yaml too.
    for table in (None, BUILTIN_LOCATIONS):
        r = evaluate(job(location=loc), PROFILE, table)
        assert r['status'] == 'ineligible' and r['evidence'].startswith(LOC_RESTRICTED_PREFIX)


def test_missing_table_scan_eliminates_hybrid_but_not_countries(env, capsys):
    env.jobs += [job(location='Hybrid', source_id='h', url='https://x.example/hybrid'),
                 job(location='United States', source_id='u', url='https://x.example/us')]
    rc, text = env.scan(locations=str(env.tmp / 'missing.yaml'))
    assert rc == 0
    err = capsys.readouterr().err
    assert 'ülke/şehir elemesi kapalı' in err and 'Hybrid/On-site elemesi yine çalışır' in err
    assert 'x.example/hybrid' not in text  # restricted jobs are grouped, not listed one by one
    assert 'x.example/us' in text          # countries are not eliminated without a table
    c = connect(env.db)
    rows = dict(c.execute('SELECT source_id, eligibility_status FROM jobs').fetchall())
    assert rows['h'] == 'ineligible' and rows['u'] != 'ineligible'


def test_list_rejected_includes_reviewed_ineligible(env, capsys):
    env.jobs.append(job(source_id='rv', location='Worldwide', url='https://x.example/reviewed'))
    env.scan(baseline=True)  # reported_via='baseline'
    env.jobs.append(job(source_id='nr', location='Worldwide', url='https://x.example/notreported'))
    c = connect(env.db)
    record_review(c, 1, 'INELIGIBLE', 'me', notes='no TR hiring')
    c.close()
    cli.list_jobs(Namespace(db=env.db, limit=30, rejected=True))
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if 'x.example/reviewed' in l]
    assert len(line) == 1 and ' | baseline | ' in line[0] and 'inceleme (me)' in line[0]
    c = connect(env.db)
    upsert(c, job(source_id='nr2', location='Worldwide', url='https://x.example/nr2'),
           evaluate(job(location='Worldwide'), PROFILE, TABLE))
    record_review(c, c.execute("SELECT id FROM jobs WHERE source_id='nr2'").fetchone()['id'], 'INELIGIBLE', 'me')
    c.close()
    cli.list_jobs(Namespace(db=env.db, limit=30, rejected=True))
    out = capsys.readouterr().out
    assert [l for l in out.splitlines() if 'x.example/nr2' in l][0].split(' | ')[1] == 'raporlanmadı'


def test_reevaluate_counts_only_unreviewed_newly_rejected(env, capsys):
    j1 = job(source_id='a', location='Berlin', url='https://x.example/a')
    j2 = job(source_id='b', location='Berlin', url='https://x.example/b')
    conn = connect(env.db)
    for j in (j1, j2):
        upsert(conn, j, evaluate(j, PROFILE))  # stored under permissive rules
    ids = [r['id'] for r in conn.execute('SELECT id FROM jobs ORDER BY id')]
    cli.mark_reported(conn, ids, 'report')
    record_review(conn, ids[1], 'UNKNOWN', 'me', notes='checked')  # effective status stays unknown
    conn.close()
    cli.reevaluate(Namespace(db=env.db, profile=env.prof, locations=None, dry_run=True))
    assert 'raporlanmış olup artık ineligible: 1 ' in capsys.readouterr().out


def test_us_state_code_or_is_not_a_separator():
    assert ev('Portland, OR (Remote)')['status'] == 'ineligible'
    assert ev('Portland, OR | Seattle, WA')['status'] == 'ineligible'
    assert ev('Spain or Portugal')['status'] == 'ineligible'  # lowercase " or " still splits


@pytest.mark.parametrize('loc', ['Remote - EMEA (excluding Turkey)', 'Remote, Europe except Türkiye',
                                 'Worldwide (not Turkey)'])
def test_turkiye_with_unknown_words_is_not_candidate_tr(loc):
    r = ev(loc, description='Python FastAPI')
    assert r['status'] != 'candidate_tr'
    res = classify_location(loc, TABLE)
    assert res.outcome == 'unclassified' and res.unclassified


def test_turkiye_plain_cases_still_candidate_tr():
    for loc in ('Remote, Turkey', 'Remote - Istanbul', 'Ankara'):
        assert ev(loc)['status'] == 'candidate_tr'


def test_scan_invalid_and_missing_table(env, capsys):
    bad = env.tmp / 'bad.yaml'
    bad.write_text('bogus: 1\n', encoding='utf-8')
    rc, _ = env.scan(locations=str(bad))
    assert rc == 1 and 'Locations error' in capsys.readouterr().err
    env.jobs.append(job(location='United States', source_id='z'))
    rc, text = env.scan(locations=str(env.tmp / 'missing.yaml'))
    assert rc == 0 and 'ülke/şehir elemesi kapalı' in capsys.readouterr().err
    assert 'Elenen ilanlar' not in text


def test_report_compaction_and_rejected_archive(env):
    for i in range(30):
        env.jobs.append(job(source_id=f'us{i}', location='United States (Remote)', url=f'https://x.example/us{i}'))
    env.jobs.append(job(source_id='neg', location='Remote', description='must be US-based', url='https://x.example/neg'))
    env.jobs.append(job(source_id='ok', location='Worldwide', url='https://x.example/ok'))
    rc, text = env.scan()
    assert rc == 0
    assert '- United States: 30 ilan' in text
    assert 'Elenen: 31 (konum: 30)' in text
    assert 'https://x.example/us0' not in text and 'https://x.example/neg' in text and 'https://x.example/ok' in text
    files = list((env.tmp / 'reports' / 'archive').glob('*-rejected.md'))
    assert len(files) == 1
    body = files[0].read_text(encoding='utf-8')
    assert sum(1 for l in body.splitlines() if 'https://x.example/' in l) == 31
    assert str(files[0]) in text and 'list --rejected' in text
    rc, text2 = env.scan()
    assert 'Elenen ilanlar' not in text2


def test_unclassified_footer_and_locations_command(env, capsys):
    env.jobs.append(job(source_id='n', location='Remote, Narnia', url='https://x.example/n'))
    rc, text = env.scan()
    assert '## Sınıflandırılamayan konum ifadeleri' in text and '"Remote, Narnia" (1)' in text
    rc, text2 = env.scan()  # reported already: nothing listed, so no footer
    assert 'Sınıflandırılamayan' not in text2
    cli.locations_cmd(Namespace(db=env.db, unclassified=True, limit=50, locations=None))
    out = capsys.readouterr().out
    assert 'Remote, Narnia' in out and 'narnia' in out


def test_list_rejected(env, capsys):
    env.jobs += [job(source_id='us', location='United States', url='https://x.example/us'),
                 job(source_id='ex', company='Insider One', url='https://x.example/ex'),
                 job(source_id='ok', location='Worldwide', url='https://x.example/ok')]
    env.scan()
    cli.list_jobs(Namespace(db=env.db, limit=30, rejected=True))
    out = capsys.readouterr().out
    assert 'https://x.example/us' in out and 'report' in out and 'Location restricted [United States]' in out
    assert 'x.example/ex' not in out and 'x.example/ok' not in out
    cli.list_jobs(Namespace(db=env.db, limit=0, rejected=True))
    assert 'x.example/us' in capsys.readouterr().out
    cli.list_jobs(Namespace(db=env.db, limit=30))  # unchanged: no rejected
    assert 'x.example/us' not in capsys.readouterr().out


def _snapshot(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = [tuple(r) for r in c.execute('SELECT id,last_seen_at,reviewed_status,reviewed_at,reported_at,reported_via FROM jobs ORDER BY id')]
    n = c.execute('SELECT COUNT(*) FROM review_events').fetchone()[0]
    c.close()
    return rows, n


def test_reevaluate_touches_only_heuristics(env, capsys):
    j1 = job(source_id='a', location='Berlin', url='https://x.example/a')
    j2 = job(source_id='b', location='Worldwide', url='https://x.example/b')
    conn = connect(env.db)
    for j in (j1, j2):  # stored under old (permissive) rules
        upsert(conn, j, evaluate(j, PROFILE))
    ids = [r['id'] for r in conn.execute('SELECT id FROM jobs ORDER BY id')]
    cli.mark_reported(conn, [ids[0]], 'report')
    record_review(conn, ids[1], 'UNKNOWN', 'me', notes='checked')
    conn.close()
    before = _snapshot(env.db)
    args = Namespace(db=env.db, profile=env.prof, locations=None, dry_run=True)
    assert cli.reevaluate(args) == 0
    out = capsys.readouterr().out
    assert 'değişen 2 / 2' in out and 'unknown -> ineligible: 1' in out and 'raporlanmış olup artık ineligible: 1' in out
    c = sqlite3.connect(env.db)
    assert c.execute('SELECT eligibility_status FROM jobs WHERE id=?', (ids[0],)).fetchone()[0] != 'ineligible'
    c.close()
    assert _snapshot(env.db) == before
    args.dry_run = False
    assert cli.reevaluate(args) == 0
    capsys.readouterr()
    c = sqlite3.connect(env.db)
    assert c.execute('SELECT eligibility_status, score FROM jobs WHERE id=?', (ids[0],)).fetchone() == ('ineligible', 0)
    c.close()
    assert _snapshot(env.db) == before


def test_reevaluate_bad_table(env, capsys):
    bad = env.tmp / 'bad.yaml'
    bad.write_text('bogus: 1\n', encoding='utf-8')
    assert cli.reevaluate(Namespace(db=env.db, profile=env.prof, locations=str(bad), dry_run=True)) == 1
    assert 'Locations error' in capsys.readouterr().err
