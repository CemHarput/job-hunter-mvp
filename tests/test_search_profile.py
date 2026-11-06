import sqlite3
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
import yaml

from jobhunter import cli
from jobhunter.collectors import fetch_ashby, fetch_lever
from jobhunter.db import connect, record_review, upsert
from jobhunter.evaluate import (DEFAULT_ROLES, SEARCH_REJECT_PREFIX, engagement, evaluate, pay_quote, role_ok,
                                validate_profile)
from jobhunter.locations import load_locations, region_of

ROOT = Path(__file__).resolve().parent.parent
TABLE, _ = load_locations(ROOT / 'config/locations.yaml')
PROFILE = {'exclude_companies': ['Insider One'], 'technologies': {'strong': ['python', 'fastapi']},
           'required': {'minimum_contract_months': 6}, 'low_priority_companies': ['Supabase'], 'search': {}}


def job(**kw):
    j = dict(source='ashby', source_id='1', company='ExampleCo', title='Senior Python Backend Engineer',
             url='https://jobs.example.com/1', location='Remote - EMEA', description='Python FastAPI',
             published_at=None, date_kind=None)
    j.update(kw)
    return j


def ev(profile=None, **kw):
    return evaluate(job(**kw), profile or PROFILE, TABLE)


def rejected_with(r, label):
    return r['status'] == 'ineligible' and r['evidence'].startswith(f'{SEARCH_REJECT_PREFIX}{label}')


# ---- AC1 configuration ------------------------------------------------------------------------------

@pytest.mark.parametrize('search', [
    {'bogus': 1}, {'regions': ['APAC']}, {'engagement': ['w2']}, {'remote': 'yes'}, {'turkiye_any_engagement': 1},
    {'roles': {'include': 'x'}}, {'roles': {'include': []}}, {'roles': {'nope': ['x']}}, {'regions': []}, 'text',
])
def test_invalid_search_block(search):
    from jobhunter.evaluate import validate_search
    prof = {'search': search}
    with pytest.raises(ValueError):
        validate_profile(prof)
        validate_search(prof, TABLE)


def test_search_and_legacy_gate_conflict():
    with pytest.raises(ValueError, match='worldwide_or_b2b'):
        validate_profile({'search': {}, 'required': {'worldwide_or_b2b': True}})


def test_legacy_gate_alone_warns_but_works():
    prof = {'required': {'worldwide_or_b2b': True}}
    warnings = validate_profile(prof)
    assert warnings and 'deprecated' in warnings[0]
    assert validate_profile({'search': {}}) == []
    r = evaluate(job(location='Remote, Poland'), prof, TABLE)  # old path: country restricted
    assert r['evidence'].startswith('Location restricted [Poland]')


def test_roles_replace_defaults():
    prof = dict(PROFILE, search={'roles': {'include': ['wizard']}})
    assert evaluate(job(title='Backend Engineer', description='contract role'), prof, TABLE)['status'] == 'irrelevant'
    assert evaluate(job(title='Senior Wizard', description='contract role'), prof, TABLE)['status'] != 'irrelevant'


def test_cli_invalid_search_profile_rc1(env, capsys):
    for text in ('search: {bogus: 1}\n', 'search: {regions: [APAC]}\n',
                 'search: {}\nrequired: {worldwide_or_b2b: true}\n'):
        env.prof.write_text(text, encoding='utf-8')
        assert env.scan()[0] == 1
        assert 'Profile error' in capsys.readouterr().err
    assert cli.reevaluate(Namespace(db=env.db, profile=str(env.prof), locations=None, dry_run=True)) == 1
    assert 'Profile error' in capsys.readouterr().err


def test_cli_legacy_gate_deprecation_warning(env, capsys):
    env.prof.write_text('required: {worldwide_or_b2b: true}\n', encoding='utf-8')
    assert env.scan()[0] == 0
    assert 'worldwide_or_b2b is deprecated' in capsys.readouterr().err


# ---- AC2 role policy ----------------------------------------------------------------------------------

@pytest.mark.parametrize('title', ['Engineer', 'Developer', 'Software Developer', 'Senior Developer',
                                   'Senior Software Engineer', 'DevOps Engineer', 'Data Engineer',
                                   'Full-stack Developer', 'AI/ML Engineer', 'Frontend Platform Engineer Python'])
def test_role_positive(title):
    assert role_ok(title, DEFAULT_ROLES)
    assert ev(title=title, description='contract role')['status'] != 'irrelevant'


@pytest.mark.parametrize('title', ['Sales Engineer', 'Solutions Engineer', 'Mechanical Engineer', 'Civil Engineer',
                                   'Engineering Manager', 'QA Engineer', 'Product Designer',
                                   'Software Engineer, iOS', 'Developer Advocate'])
def test_role_negative(title):
    assert not role_ok(title, DEFAULT_ROLES)
    assert ev(title=title, description='contract role')['status'] == 'irrelevant'


# ---- AC3 remote, AC4 region -------------------------------------------------------------------------

def test_remote_tests():
    r = ev(location='Hybrid - EMEA', description='contract role')
    assert rejected_with(r, 'On-site/hybrid]')
    r = ev(location='Berlin, Germany', description='This is a fully remote contract role')
    assert r['status'] == 'unknown' and r['search']['kind'] == 'country'
    assert rejected_with(ev(location='', description='contract role'), 'Uzaktan belirtilmemiş]')


SHOTS = [  # (location, title, description, expected labels)
    ('Remote (UK or Berlin, Germany)', 'AI Engineer / Senior AI Engineer', 'Type: Contract (6 to 8 months)', ['Germany', 'United Kingdom']),
    ('United Kingdom (Remote)', 'Senior Software Developer (12 Month FTC, £80k)', 'Python', ['United Kingdom']),
    ('Ukraine (Remote)', 'Senior AI/ML Engineer (GenAI, AWS)', 'B2B contract', ['Ukraine']),
    ('Germany (Remote)', 'Senior Backend Application Engineer - Python/AWS/GenAI', 'Python', ['Germany']),
    ('South Africa (Remote)', 'Senior Backend Engineer', 'We hire an independent contractor', ['South Africa']),
    ('Poland (Remote)', 'Senior Software Engineer', 'Employment type: B2B', ['Poland']),
]


@pytest.mark.parametrize('loc,title,desc,labels', SHOTS)
def test_screenshot_cases_pass_as_country(loc, title, desc, labels):
    kw = {'employment_type': 'Contract'} if loc.startswith('Germany') else {}
    r = ev(location=loc, title=title, description=desc, **kw)
    assert r['status'] == 'unknown', r
    assert r['search']['kind'] == 'country' and r['search']['labels'] == labels
    assert 'ülke bazlı EMEA' in r['search']['note'] and 'Türkiye' in r['search']['note']
    assert r['status'] != 'VERIFIED_TR' and r['status'] != 'candidate_tr'


def test_region_kinds():
    expect = {'Remote - EMEA': 'regional', 'Europe, Middle East and Africa (Remote)': 'regional', 'Worldwide (Remote)': 'open',
              'Remote, Turkey': 'turkiye', 'Remote, EU': 'country', 'United States (Remote)': 'outside',
              'Canada (Remote)': 'outside', 'India (Remote)': 'outside', 'Americas (Remote)': 'outside',
              'APAC (Remote)': 'outside', 'Remote': 'unknown', 'Remote in Germany': 'country', 'Remote, Narnia': 'unknown'}
    for loc, kind in expect.items():
        assert region_of(loc, TABLE, 'EMEA').kind == kind, loc
    assert region_of('Hybrid - Berlin', TABLE, 'EMEA').onsite_only


def test_region_rejections():
    r = ev(location='United States (Remote)', description='contract role')
    assert rejected_with(r, 'EMEA dışı: United States]')
    assert rejected_with(ev(location='Remote', description='contract role'), 'Bölge belirsiz]')
    # the description can supply the region for a bare "Remote"
    assert ev(location='Remote', description='contract role. Remote - Europe')['search']['kind'] == 'regional'
    assert ev(location='Remote', description='contract role. We hire worldwide remote')['search']['kind'] == 'open'


# ---- AC5 geographic text ----------------------------------------------------------------------------

def test_text_restrictions():
    r = ev(location='Remote', description='Candidates must be based in the UK. contract role')
    assert r['status'] == 'unknown' and r['search']['kind'] == 'country'
    assert 'must be based in the UK' in r['search']['note']
    r = ev(location='Remote - EMEA', description='You must be US-based. contract role')
    assert r['status'] == 'ineligible' and r['evidence'].startswith('Explicit geographic')
    r = ev(location='Remote - EMEA', description='contract role. Hybrid role in the office')
    assert r['status'] == 'ineligible' and r['evidence'].startswith('Explicit geographic')
    # old path unchanged: UK-only text is still a restriction without a search block
    old = evaluate(job(location='Remote', description='must be based in the UK'), {}, TABLE)
    assert old['status'] == 'ineligible'


# ---- AC6 engagement --------------------------------------------------------------------------------

def test_engagement_negatives():
    r = ev(employment_type='FullTime')
    assert rejected_with(r, 'B2B/Contract sinyali yok]') and 'employment_type="FullTime"' in r['evidence']
    assert rejected_with(ev(description='We sell to B2B SaaS companies'), 'B2B/Contract sinyali yok]')
    assert rejected_with(ev(title='Smart Contract Engineer', description='solidity'), 'B2B/Contract sinyali yok]')
    assert rejected_with(ev(description='This is not a contract role'), 'B2B/Contract sinyali yok]')
    assert rejected_with(ev(description='Permanent role, no contractor basis'), 'B2B/Contract sinyali yok]')
    assert rejected_with(ev(description='Our contract management software'), 'B2B/Contract sinyali yok]')


def test_engagement_field_values():
    assert ev(employment_type=['full time', 'contract'])['search']['engagement'] == 'alan: employment_type="contract"'
    for v in (None, '', 123, [], [1, None]):
        assert rejected_with(ev(employment_type=v), 'B2B/Contract sinyali yok]')
    for v in ('Contract', 'Contractor', 'Freelance', 'fixed-term', 'Temporary', 'B2B'):
        assert ev(employment_type=v)['status'] == 'global_unconfirmed', v
    assert engagement(job(), ['b2b']) == (False, None, None)
    assert engagement(job(employment_type='Contract'), ['b2b'])[0] is False  # kinds are respected


@pytest.mark.parametrize('desc', ['B2B contract', 'Employment type: B2B', 'on a B2B basis', 'B2B or UoP', 'independent contractor',
                                  'work as a contractor', 'freelance role', 'contract position', 'Type: Contract',
                                  'Contract (6 to 8 months)', '12-month contract', 'a fixed-term role', 'contract-to-hire'])
def test_engagement_phrases(desc):
    assert engagement(job(description=f'Python. {desc}. Apply.'), ['b2b', 'contract'])[0], desc


def test_engagement_title_and_ftc():
    assert engagement(job(title='Python Engineer (Contract)'), ['contract'])[0]
    assert engagement(job(title='Engineer, 12 Month FTC'), ['contract'])[0]
    assert not engagement(job(description='ftc approval'), ['contract'])[0]  # FTC is case-sensitive


# ---- AC7 Türkiye exception -------------------------------------------------------------------------

def test_turkiye_exception():
    r = ev(location='Remote, Turkey', description='Python')
    assert r['status'] == 'candidate_tr' and r['search']['section'] == 'turkiye'
    prof = dict(PROFILE, search={'turkiye_any_engagement': False})
    assert rejected_with(ev(prof, location='Remote, Turkey', description='Python'), 'B2B/Contract sinyali yok]')
    assert ev(prof, location='Remote, Turkey', description='contract role')['search']['section'] == 'main'


# ---- AC8 contract length ---------------------------------------------------------------------------

def test_contract_length():
    r = ev(description='3-month contract')
    assert r['status'] == 'ineligible' and 'Stated contract length 3 months < required 6' in r['evidence']
    r = ev(location='Remote (UK or Berlin, Germany)', description='Type: Contract (6 to 8 months)')
    assert r['status'] == 'unknown'
    r = ev(title='Software Engineer (3 Month FTC)')
    assert 'Stated contract length 3 months' in r['evidence']
    r = ev(description='contract role')
    assert r['status'] == 'global_unconfirmed' and 'months' not in r['search']['note']


# ---- AC9 ranking, pay ------------------------------------------------------------------------------

def test_ranking_and_pay_does_not_score():
    kw = dict(description='Python FastAPI contract role')
    s = {k: ev(location=loc, **kw)['score'] for k, loc in
         [('tr', 'Remote, Turkey'), ('open', 'Worldwide (Remote)'), ('reg', 'Remote - EMEA'), ('cty', 'United Kingdom (Remote)')]}
    assert s['tr'] > s['open'] > s['reg'] > s['cty'] > 0 and max(s.values()) <= 100
    rich = ev(location='Remote - EMEA', description='Python FastAPI contract role. Salary $200k', salary='$200k')
    assert rich['score'] == s['reg']
    assert ev(company='Supabase', location='Remote - EMEA', **kw)['score'] == s['reg'] - 20


def test_pay_quote():
    assert pay_quote(job(title='Senior Software Developer (12 Month FTC, £80k)')) == '£80k'
    assert pay_quote(job(description='Rate: $70-80/hr on B2B')) == '$70-80/hr'
    assert pay_quote(job(salary='  EUR 60 000 gross ')) == 'EUR 60 000 gross'
    assert pay_quote(job(description='Python. Compensation: $120K - $150K')) == '$120K - $150K'
    assert pay_quote(job(description='Python. Compensation: ')) is None
    assert pay_quote(job(description='We raised $5 million')) is None


# ---- AC10 report -----------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    jobs = []
    monkeypatch.setattr(cli, 'collect', lambda sources: (list(jobs), []))
    prof = tmp_path / 'profile.yaml'
    prof.write_text('exclude_companies: [Insider One]\nrequired: {minimum_contract_months: 6}\nsearch: {}\n', encoding='utf-8')
    src = tmp_path / 'sources.yaml'
    src.write_text('{}', encoding='utf-8')

    def scan(**kw):
        a = dict(db=str(tmp_path / 'j.sqlite3'), profile=str(prof), sources=str(src),
                 output=str(tmp_path / 'reports' / 'latest.md'), email=False, env_file=str(tmp_path / 'none.env'),
                 baseline=False, max_age_days=None)
        a.update(kw)
        rc = cli.run_scan(Namespace(**a))
        out = tmp_path / 'reports' / 'latest.md'
        return rc, out.read_text(encoding='utf-8') if out.exists() else ''

    return Namespace(jobs=jobs, scan=scan, tmp=tmp_path, db=str(tmp_path / 'j.sqlite3'), prof=prof)


def _mixed_jobs():
    return [
        job(source_id='uk', company='UKCo', title='Senior Software Developer (12 Month FTC, £80k)', location='United Kingdom (Remote)',
            url='https://x.example/uk', description='Python'),
        job(source_id='de', company='DeCo', location='Germany (Remote)', url='https://x.example/de', employment_type='Contract',
            description='Python | pipes\nand newlines'),
        job(source_id='em', company='EmeaCo', location='Remote - EMEA', url='https://x.example/em', description='B2B contract. Python',
            source_name='Remotive', apply_url='https://x.example/apply'),
        job(source_id='tr', company='TrCo', location='Remote, Turkey', url='https://x.example/tr', description='Python'),
        job(source_id='us1', location='United States (Remote)', url='https://x.example/us1', description='contract role'),
        job(source_id='us2', location='United States (Remote)', url='https://x.example/us2', description='contract role'),
        job(source_id='us3', location='United States (Remote)', url='https://x.example/us3', description='contract role'),
        job(source_id='ns1', location='Remote - EMEA', url='https://x.example/ns1', description='Permanent'),
        job(source_id='ns2', location='Remote - EMEA', url='https://x.example/ns2', employment_type='FullTime'),
    ]


def test_report_tables(env):
    env.jobs += _mixed_jobs()
    rc, text = env.scan()
    assert rc == 0
    assert '## EMEA · Remote · B2B/Contract (3)' in text
    assert '| Şirket | Pozisyon | Konum | Çalışma şekli (kanıt) | Ücret | Kaynak | İlk görülme | Uygunluk notu | Skor |' in text
    main = text.split('## Türkiye')[0]
    uk = [l for l in main.splitlines() if 'UKCo' in l][0]
    assert '£80k' in uk and '12 Month FTC' in uk and 'ülke bazlı EMEA (United Kingdom)' in uk
    de = [l for l in main.splitlines() if 'DeCo' in l][0]
    assert 'employment_type="Contract"' in de and 'Doğrulanmadı' in de and de.count('|') == 10
    em = [l for l in main.splitlines() if 'EmeaCo' in l][0]
    assert 'ashby — https://x.example/em' in em and 'EMEA/bölgesel' in em  # source_name/apply_url are not stored (yet)
    row = cli._search_row(dict(company='C', title='T', source='remotive', url='https://u', source_name='Remotive',
                               apply_url='https://apply', first_seen_at='2026-01-02T00:00', score=5, reviewed_status=None,
                               eligibility_status='unknown'), None)
    assert 'Remotive — https://apply' in row and '2026-01-02' in row
    assert '## Türkiye · Remote / elle incelenmiş (1)' in text and 'TrCo' in text.split('## Türkiye')[1]
    assert '- EMEA dışı: United States: 3 ilan' in text
    assert '- B2B/Contract sinyali yok: 2 ilan' in text
    assert 'arama profili: 5' in text and 'x.example/us1' not in text
    rej = next(env.tmp.glob('reports/archive/*-rejected.md')).read_text(encoding='utf-8')
    assert len([l for l in rej.splitlines() if l.startswith('#') and ' | ' in l and 'x.example' in l]) == 5


def test_first_seen_date_in_row(env):
    env.jobs.append(job(source_id='em', location='Remote - EMEA', description='B2B contract'))
    _, text = env.scan()
    c = connect(env.db)
    first = c.execute('SELECT first_seen_at FROM jobs').fetchone()[0][:10]
    assert f'| {first} |' in text


def test_list_rejected_shows_all(env, capsys):
    env.jobs += _mixed_jobs()
    env.scan()
    cli.list_jobs(Namespace(db=env.db, limit=0, rejected=True))
    out = capsys.readouterr().out
    for sid in ('us1', 'us2', 'us3', 'ns1', 'ns2'):
        assert f'x.example/{sid}' in out
    assert 'Search profile [EMEA dışı: United States]' in out


def test_empty_list_message(env):
    env.jobs.append(job(source_id='us1', location='United States (Remote)', description='contract role'))
    rc, text = env.scan()
    assert '## EMEA · Remote · B2B/Contract (0)' in text
    assert 'ATS panoları B2B/contract ifadesini nadiren belirtir' in text and 'job-discovery-sources' in text


def test_old_report_unchanged_without_search(env):
    env.prof.write_text('exclude_companies: [Insider One]\n', encoding='utf-8')
    env.jobs.append(job(location='Worldwide', description='Python'))
    rc, text = env.scan()
    assert '## ExampleCo — Senior Python Backend Engineer' in text and 'EMEA · Remote' not in text
    assert 'arama profili' not in text


# ---- AC11 reevaluate -------------------------------------------------------------------------------

def _snapshot(db):
    c = sqlite3.connect(db)
    rows = [tuple(r) for r in c.execute('SELECT id,last_seen_at,reviewed_status,reviewed_at,reported_at,reported_via FROM jobs ORDER BY id')]
    n = c.execute('SELECT COUNT(*) FROM review_events').fetchone()[0]
    c.close()
    return rows, n


def test_reevaluate_search_profile(env, capsys):
    env.prof.write_text('exclude_companies: [Insider One]\n', encoding='utf-8')  # old rules store everything
    stored = [job(source_id='a', location='Remote - EMEA', url='https://x.example/a', description='B2B contract'),
              job(source_id='b', location='United States (Remote)', url='https://x.example/b'),
              job(source_id='c', location='Remote - EMEA', url='https://x.example/c', description='Permanent', employment_type='Contract')]
    conn = connect(env.db)
    for j in stored:
        upsert(conn, j, evaluate(j, {}, TABLE))
    cli.mark_reported(conn, [3], 'report')
    record_review(conn, 2, 'UNKNOWN', 'me', notes='checked')
    stored_et = conn.execute('SELECT employment_type FROM jobs WHERE id=3').fetchone()[0]
    conn.close()
    assert stored_et == 'Contract'
    env.prof.write_text('search: {}\n', encoding='utf-8')
    before = _snapshot(env.db)
    args = Namespace(db=env.db, profile=str(env.prof), locations=None, dry_run=True)
    assert cli.reevaluate(args) == 0
    out = capsys.readouterr().out
    assert 'Arama profili: geçen 2 / 3 (raporlanmamış+başvurulmamış: 1)' in out
    assert '  red: EMEA dışı: United States: 1' in out
    assert _snapshot(env.db) == before
    c = sqlite3.connect(env.db)
    assert not c.execute('SELECT eligibility_evidence FROM jobs WHERE id=1').fetchone()[0].startswith('EMEA/bölgesel')  # dry-run wrote nothing
    c.close()
    args.dry_run = False
    assert cli.reevaluate(args) == 0
    capsys.readouterr()
    c = sqlite3.connect(env.db)
    assert c.execute('SELECT eligibility_evidence FROM jobs WHERE id=1').fetchone()[0].startswith('EMEA/bölgesel')
    assert c.execute('SELECT eligibility_status FROM jobs WHERE id=2').fetchone()[0] == 'ineligible'
    assert c.execute('SELECT eligibility_status FROM jobs WHERE id=3').fetchone()[0] == 'global_unconfirmed'
    c.close()
    assert _snapshot(env.db) == before


# ---- AC12 data fields ------------------------------------------------------------------------------

def test_migration_adds_columns_and_upsert_stores(tmp_path):
    db = tmp_path / 'old.sqlite3'
    c = sqlite3.connect(db)
    c.executescript('''CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
        company TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, location TEXT, description TEXT, published_at TEXT,
        date_kind TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        eligibility_status TEXT NOT NULL DEFAULT 'unknown', eligibility_evidence TEXT, score INTEGER NOT NULL DEFAULT 0,
        technologies_json TEXT NOT NULL DEFAULT '[]', UNIQUE(source, source_id));''')
    c.commit()
    c.close()
    conn = connect(db)
    cols = {r[1] for r in conn.execute('PRAGMA table_info(jobs)')}
    assert {'employment_type', 'salary'} <= cols
    j = job(employment_type=['contract', 'Remote OK'], salary='$70-80/hr')
    upsert(conn, j, evaluate(j, PROFILE, TABLE))
    assert tuple(conn.execute('SELECT employment_type, salary FROM jobs').fetchone()) == ('contract, Remote OK', '$70-80/hr')
    j2 = job(employment_type=None)
    upsert(conn, j2, evaluate(j2, PROFILE, TABLE))
    assert tuple(conn.execute('SELECT employment_type, salary FROM jobs').fetchone()) == (None, None)


def _client(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))


def test_ashby_and_lever_fields():
    ashby = {'jobs': [{'id': 'a1', 'title': 'Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/x/a1', 'location': 'Remote',
                       'descriptionHtml': '<p>Hi</p>', 'employmentType': 'Contract',
                       'compensation': {'compensationTierSummary': '$100K - $120K'}},
                      {'id': 'a2', 'title': 'Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/x/a2', 'location': 'Remote',
                       'employmentType': 'FullTime', 'compensation': {'compensationTierSummary': None}}]}
    a1, a2 = list(fetch_ashby(_client(ashby), 'x', 'X'))
    assert (a1['employment_type'], a1['salary']) == ('Contract', '$100K - $120K')
    assert (a2['employment_type'], a2['salary']) == ('FullTime', None)
    lever = [{'id': 'l1', 'text': 'Engineer', 'hostedUrl': 'https://jobs.lever.co/x/l1', 'categories': {'location': 'Remote', 'commitment': 'Short Term'}},
             {'id': 'l2', 'text': 'Engineer', 'hostedUrl': 'https://jobs.lever.co/x/l2', 'categories': {}}]
    l1, l2 = list(fetch_lever(_client(lever), 'x', 'X'))
    assert l1['employment_type'] == 'Short Term' and l2['employment_type'] is None


# ---- locations additions ---------------------------------------------------------------------------

def test_default_regions_without_section():
    t, _ = load_locations(ROOT / 'config/locations.yaml')
    assert 'emea' in t.regions and 'germany' in t.regions['emea'] and 'africa' in t.regions['emea']


def test_regions_validation(tmp_path):
    from jobhunter.locations import build_table
    ok = build_table(yaml.safe_load('restricted:\n  Narnia: []\nregional: [Foo]\nregions:\n  R: [Narnia, Foo]\n'))
    assert ok.regions == {'r': {'narnia', 'foo'}}
    for bad in ('regions:\n  R: [Nowhere]\n', 'regions: [R]\n', 'regions:\n  R: []\n', 'regions: {}\n'):
        with pytest.raises(ValueError):
            build_table(yaml.safe_load(bad))


# ---- engagement test switched off (`engagement: []`) -------------------------------------------------------

OFF = dict(PROFILE, search={'engagement': []})


@pytest.mark.parametrize('loc', ['Remote - EMEA', 'Worldwide Remote', 'Poland (Remote)', 'United Kingdom (Remote)'])
def test_engagement_off_lists_emea_remote_without_b2b(loc):
    r = ev(OFF, location=loc, description='Python FastAPI. Permanent role.')
    assert r['status'] != 'ineligible', r['evidence']
    assert r['status'] != 'VERIFIED_TR'


def test_engagement_off_still_applies_region_and_remote():
    assert rejected_with(ev(OFF, location='United States (Remote)', description='Python'), 'EMEA dışı')
    assert rejected_with(ev(OFF, location='Berlin, Germany', description='Python. Office based.'), 'Uzaktan')
    assert rejected_with(ev(OFF, location='Hybrid - EMEA', description='Python'), 'On-site/hybrid')


def test_engagement_off_stated_contract_still_ranks_higher_and_short_contract_still_rejected():
    plain = ev(OFF, location='Remote - EMEA', description='Python FastAPI')
    contract = ev(OFF, location='Remote - EMEA', description='Python FastAPI. B2B contract.')
    assert contract['score'] == plain['score'] + 10
    short = ev(OFF, location='Remote - EMEA', description='Python. 3-month contract.')
    assert short['status'] == 'ineligible' and 'Stated contract length 3 months' in short['evidence']


def test_engagement_off_country_note_does_not_claim_b2b():
    r = ev(OFF, location='Poland (Remote)', description='Python. Permanent role.')
    assert r['search']['kind'] == 'country'
    assert 'B2B/contract ifadesi yok' in r['search']['note'] and 'ikamet' in r['search']['note']
    r2 = ev(OFF, location='Poland (Remote)', description='Python. B2B contract.')
    assert 'B2B/contract kanıtı var' in r2['search']['note']


def test_engagement_default_and_explicit_list_still_require_it():
    assert rejected_with(ev(location='Remote - EMEA', description='Python. Permanent.'), 'B2B/Contract')
    explicit = dict(PROFILE, search={'engagement': ['b2b', 'contract']})
    assert rejected_with(ev(explicit, location='Remote - EMEA', description='Python. Permanent.'), 'B2B/Contract')


def test_report_title_without_engagement(tmp_path, monkeypatch):
    jobs = [job(source_id='1', location='Remote - EMEA', url='https://jobs.example.com/1')]
    monkeypatch.setattr(cli, 'collect', lambda sources: (list(jobs), []))
    (tmp_path / 'p.yaml').write_text('search:\n  engagement: []\n', encoding='utf-8')
    (tmp_path / 's.yaml').write_text('{}', encoding='utf-8')
    out = tmp_path / 'r' / 'latest.md'
    rc = cli.run_scan(Namespace(db=str(tmp_path / 'j.sqlite3'), profile=str(tmp_path / 'p.yaml'), sources=str(tmp_path / 's.yaml'),
                                output=str(out), email=False, env_file=str(tmp_path / 'none'), baseline=False, max_age_days=None,
                                locations=str(ROOT / 'config/locations.yaml')))
    text = out.read_text(encoding='utf-8')
    assert rc == 0 and '## EMEA · Remote (1)' in text and 'B2B/Contract (' not in text
    assert 'https://jobs.example.com/1' in text


def test_list_hides_applied_jobs(env, capsys):
    env.jobs += [job(source_id='a', company='AppliedCo', title='Backend Engineer', location='Remote - EMEA', url='https://x.example/a'),
                 job(source_id='b', company='OtherCo', title='Backend Engineer', location='Remote - EMEA', url='https://x.example/b')]
    PROFILE_OFF = 'search:\n  engagement: []\n'
    env.prof.write_text(PROFILE_OFF, encoding='utf-8')
    env.scan()
    c = connect(env.db)
    c.execute("INSERT INTO applications(company,title,url,status,applied_at) VALUES('AppliedCo','Backend Engineer','https://x.example/a','applied','2026-11-06')")
    c.commit(); c.close()
    cli.list_jobs(Namespace(db=env.db, limit=30))
    out = capsys.readouterr().out
    assert 'OtherCo' in out and 'AppliedCo' not in out
