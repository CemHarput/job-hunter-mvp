import os
import sqlite3
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest

from jobhunter import cli
from jobhunter.db import connect, effective_status, record_review, upsert
from jobhunter.evaluate import TECH, contract_months, evaluate, validate_profile

PROFILE = {'exclude_companies': ['Insider One'], 'technologies': {'strong': ['python', 'fastapi']},
           'required': {'minimum_contract_months': 6}, 'low_priority_companies': ['Supabase']}


def job(**kw):
    j = dict(source='ashby', source_id='1', company='ExampleCo', title='Senior Python Backend Engineer',
             url='https://jobs.example.com/1', location='Remote worldwide', description='Python FastAPI',
             published_at=None, date_kind=None)
    j.update(kw)
    return j


def st(**kw):
    return evaluate(job(**kw), PROFILE)['status']


def iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    jobs = []
    sent = []
    monkeypatch.setattr(cli, 'collect', lambda sources: (list(jobs), []))
    monkeypatch.setattr(cli, 'send_email', lambda s, b: sent.append(s))
    prof = tmp_path / 'profile.yaml'
    prof.write_text('exclude_companies: [Insider One]\nlow_priority_companies: [Supabase]\n', encoding='utf-8')
    src = tmp_path / 'sources.yaml'
    src.write_text('{}', encoding='utf-8')

    def scan(**kw):
        a = dict(db=str(tmp_path / 'j.sqlite3'), profile=str(prof), sources=str(src),
                 output=str(tmp_path / 'reports' / 'latest.md'), email=False, env_file=str(tmp_path / 'none.env'),
                 baseline=False, max_age_days=None)
        a.update(kw)
        rc = cli.run_scan(Namespace(**a))
        return rc, (tmp_path / 'reports' / 'latest.md').read_text(encoding='utf-8')

    return Namespace(jobs=jobs, sent=sent, scan=scan, tmp=tmp_path, db=str(tmp_path / 'j.sqlite3'))


def test_review_survives_rescan(tmp_path):
    c = connect(tmp_path / 'a.sqlite3')
    j = job(); e = evaluate(j, PROFILE)
    upsert(c, j, e)
    jid = c.execute('SELECT id FROM jobs').fetchone()['id']
    record_review(c, jid, 'VERIFIED_TR', 'me', 'https://x.example/p', 'Türkiye ok')
    upsert(c, j, e)
    r = c.execute('SELECT * FROM jobs').fetchone()
    assert r['reviewed_status'] == 'VERIFIED_TR' and effective_status(r) == 'VERIFIED_TR'
    assert r['eligibility_status'] == 'global_unconfirmed'
    assert c.execute('SELECT count(*) FROM review_events').fetchone()[0] == 1


def test_review_validation_and_alias(tmp_path):
    c = connect(tmp_path / 'a.sqlite3')
    upsert(c, job(), evaluate(job(), PROFILE))
    for url, quote in [(None, 'q'), ('http://x', 'q'), ('https://x.example', ''), ('https://x.example', None)]:
        with pytest.raises(ValueError):
            record_review(c, 1, 'VERIFIED_TR', 'me', url, quote)
    assert c.execute('SELECT count(*) FROM review_events').fetchone()[0] == 0
    assert record_review(c, 1, 'REMOTE_WORLDWIDE_UNCONFIRMED', 'me') == 'GLOBAL_UNCONFIRMED'
    with pytest.raises(ValueError):
        record_review(c, 99, 'UNKNOWN', 'me')
    with pytest.raises(ValueError):
        record_review(c, 1, 'BOGUS', 'me')


def test_review_cli_exit_codes(env):
    env.jobs.append(job())
    env.scan()
    base = ['--db', env.db]
    ns = lambda **k: Namespace(db=env.db, job_id=1, url=None, status='VERIFIED_TR', reviewer='me',
                                evidence_url=None, evidence_quote=None, notes=None, **k)
    assert cli.review(ns()) == 1
    a = ns(); a.evidence_url, a.evidence_quote = 'https://x.example', 'quote'
    assert cli.review(a) == 0
    a = ns(); a.job_id, a.url = None, 'https://nope'
    assert cli.review(a) == 1


def test_verified_excluded_company_stays_out(env, capsys):
    env.jobs.append(job(company='Insider One'))
    env.scan()
    c = connect(env.db)
    jid = c.execute('SELECT id FROM jobs').fetchone()['id']
    record_review(c, jid, 'VERIFIED_TR', 'me', 'https://x.example', 'q')
    assert effective_status(c.execute('SELECT * FROM jobs').fetchone()) == 'excluded'
    c.close()
    _, text = env.scan()
    assert 'Insider One' not in text
    cli.list_jobs(Namespace(db=env.db, limit=10))
    assert 'Insider One' not in capsys.readouterr().out


def test_email_failure_keeps_jobs_unreported(env, monkeypatch):
    env.jobs.append(job())
    def boom(s, b): raise RuntimeError('smtp down')
    monkeypatch.setattr(cli, 'send_email', boom)
    rc, text = env.scan(email=True)
    assert rc == 1 and 'ExampleCo' in text
    c = connect(env.db)
    assert c.execute('SELECT reported_at FROM jobs').fetchone()[0] is None
    monkeypatch.setattr(cli, 'send_email', lambda s, b: env.sent.append(s))
    rc, text = env.scan(email=True)
    assert rc == 0 and 'ExampleCo' in text
    assert c.execute('SELECT reported_via FROM jobs').fetchone()[0] == 'email'
    rc, text = env.scan(email=True)
    assert 'ExampleCo' not in text


def test_archive_written(env):
    env.jobs.append(job())
    env.scan()
    assert list((env.tmp / 'reports' / 'archive').glob('scan-*.md'))


def test_baseline(env):
    env.jobs.append(job())
    rc, text = env.scan(baseline=True)
    assert rc == 0 and 'ExampleCo' not in text.split('Baseline')[0] and '## ExampleCo' not in text
    c = connect(env.db)
    assert c.execute('SELECT reported_via FROM jobs').fetchone()[0] == 'baseline'
    env.jobs.append(job(source_id='2', title='Backend Engineer', url='https://jobs.example.com/2'))
    _, text = env.scan()
    assert '## ExampleCo — Backend Engineer' in text and 'Senior Python' not in text


def test_age_filter(env):
    env.jobs += [job(source_id='old', title='Old Backend', published_at=iso(100)),
                 job(source_id='new', title='New Backend', published_at=iso(2)),
                 job(source_id='nodate', title='Nodate Backend', published_at=None),
                 job(source_id='bad', title='Bad Backend', published_at='garbage')]
    _, text = env.scan(max_age_days=30)
    assert 'New Backend' in text and 'Nodate Backend' in text and 'Bad Backend' in text
    assert '## ExampleCo — Old Backend' not in text
    c = connect(env.db)
    assert c.execute("SELECT reported_at FROM jobs WHERE source_id='old'").fetchone()[0] is None


def test_ineligible_section_and_low_priority(env):
    env.jobs += [job(source_id='a', title='Backend Engineer', location='Remote, must be US-based', url='https://jobs.example.com/rejected'),
                 job(source_id='b', company='Supabase', title='Python Backend Engineer', url='https://jobs.example.com/lowprio')]
    _, text = env.scan()
    assert 'Elenen ilanlar (ineligible)' in text
    assert 'https://jobs.example.com/rejected' in text.split('Elenen ilanlar')[1]
    assert 'https://jobs.example.com/rejected' not in text.split('Elenen ilanlar')[0]
    assert 'US-based' in text and 'Düşük öncelik' in text
    _, text = env.scan()
    assert 'Elenen ilanlar' not in text


@pytest.mark.parametrize('title', ['JavaScript Developer', 'Frontend Engineer', 'Software Engineer, iOS',
                                   'Senior Software Engineer - Mobile', 'Product Designer Software Engineer'])
def test_irrelevant_titles(title):
    assert st(title=title) == 'irrelevant'


@pytest.mark.parametrize('title', ['Senior Java Engineer', 'Backend Engineer', 'Software Engineer, Platform',
                                   'Frontend Platform Engineer Python'])
def test_relevant_titles(title):
    assert st(title=title) != 'irrelevant'


def test_tech_word_boundaries():
    m = {x.group(0).lower() for x in TECH.finditer('JavaScript drag laws aws Java rag LLMs')}
    assert m == {'aws', 'java', 'rag', 'llms'}
    assert evaluate(job(title='Backend Engineer', description='JavaScript drag laws LLMs'), PROFILE)['tech'] == ['llm']


@pytest.mark.parametrize('loc,desc', [('Remote', 'Must be US-based'), ('Remote', 'You are authorized to work in the United States'),
                                       ('EU only', ''), ('Remote', 'Remote within the EU'), ('Remote', 'US-based candidates preferred'),
                                       ('Remote', 'Must be located in the U.S.')])
def test_new_negative(loc, desc):
    r = evaluate(job(location=loc, description=desc), PROFILE)
    assert r['status'] == 'ineligible' and '"' in r['evidence']


@pytest.mark.parametrize('desc', ['Join us only if you love Python', 'Remote worldwide', 'Remote in Europe', 'Work with us only on weekdays'])
def test_not_negative(desc):
    assert st(location='Remote', description=desc) != 'ineligible'


def test_contract_months():
    assert contract_months('3-month contract') == 3
    assert contract_months('6 to 9 month contract') == 9
    assert contract_months('12-month contract') == 12
    assert contract_months('3 months probation, permanent role; fixed term contract') is None
    assert contract_months('1 year contract') == 12
    assert st(description='Python. 3-month contract') == 'ineligible'
    assert st(description='Python. 12-month contract') != 'ineligible'
    assert st(description='Python. Permanent role with 3 months probation') != 'ineligible'


def test_long_term_not_a_short_contract():
    assert contract_months('This is a long-term, permanent role. In your first 3 months you will own ingestion.') is None
    assert contract_months('Interview duration is 2 months; we hire contractors worldwide') is None
    assert contract_months('Contract length: 3 months') == 3
    assert contract_months('contract of 4 months') == 4
    assert contract_months('3 month fixed-term contract') == 3
    assert st(description='Python. Long-term permanent role. In your first 3 months you will own the service.') != 'ineligible'


# Conservative by design: naming Türkiye never rescues a restriction match. Multi-country lists
# are eliminated but stay visible in the report's "Elenen ilanlar" section for manual review.
@pytest.mark.parametrize('desc', ['Remote in the US only; we cannot hire in Türkiye',
                                  'Must be based in the US. We also hire in Turkey for other teams.',
                                  'Not remote. On-site role in our Istanbul, Türkiye office',
                                  'Remote in the US, not open to candidates in Turkey',
                                  'EU only, Turkey excluded',
                                  'Must be based in the US or Canada, Turkey not eligible',
                                  'Remote in the EU, Türkiye excluded',
                                  'Remote in the EU or Türkiye',
                                  'Must be based in the UK, EU or Turkey',
                                  'Remote in the UK, Spain, Portugal or Türkiye'])
def test_turkiye_never_rescues_restrictions(desc):
    r = evaluate(job(location='Remote', description=desc), PROFILE)
    assert r['status'] == 'ineligible'
    assert r['status'] != 'candidate_tr'


def test_multi_country_list_listed_in_rejected_section(env):
    env.jobs.append(job(title='Backend Engineer', location='Remote', description='Remote in the EU or Türkiye',
                        url='https://jobs.example.com/multi'))
    _, text = env.scan()
    assert 'https://jobs.example.com/multi' in text.split('Elenen ilanlar')[1]
    assert 'Remote in the EU' in text.split('Elenen ilanlar')[1]


@pytest.mark.parametrize('text,months', [('contract (3 months)', 3), ('contract (6 months)', 6), ('contract for 3 months', 3),
                                         ('contract position for 3 months', 3), ('Contract role, 3 months', 3),
                                         ('3 months (contract)', 3), ('3-months contract', 3), ('3 month contract', 3),
                                         ('Contract length: 3 months. Experience with Python required', 3)])
def test_common_short_contract_phrasings(text, months):
    assert contract_months(text) == months
    # PROFILE requires >= 6 months, so only shorter stated contracts are eliminated
    assert (st(description='Python. ' + text) == 'ineligible') == (months < 6)


def test_contract_skip_is_word_bound_and_sentence_bound():
    assert contract_months('Industrial setting, 3-month contract') == 3
    assert contract_months('3-month probation. Then a contract') is None


def test_legacy_status_case_normalised(tmp_path):
    p = tmp_path / 'old.sqlite3'
    raw = sqlite3.connect(p)
    raw.executescript('''CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
        company TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, location TEXT, description TEXT, published_at TEXT,
        date_kind TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        eligibility_status TEXT NOT NULL DEFAULT 'unknown', eligibility_evidence TEXT, score INTEGER NOT NULL DEFAULT 0,
        technologies_json TEXT NOT NULL DEFAULT '[]', UNIQUE(source, source_id));
        INSERT INTO jobs(source,source_id,company,title,url,first_seen_at,last_seen_at,eligibility_status)
        VALUES ('a','1','C','T','u','2026-01-01','2026-01-01','Verified_TR');''')
    raw.commit(); raw.close()
    c = connect(p)
    assert c.execute('SELECT reviewed_status FROM jobs').fetchone()[0] == 'VERIFIED_TR'


def test_multiline_quote_stays_on_one_line(env):
    env.jobs.append(job())
    env.scan(baseline=True)
    c = connect(env.db)
    record_review(c, 1, 'VERIFIED_TR', 'me', 'https://careers.example/tr', 'line one\nline two')
    c.execute('UPDATE jobs SET reported_at=NULL, reported_via=NULL')
    c.commit()
    _, text = env.scan()
    assert '"line one line two"' in text


def test_review_evidence_shown_in_report(env):
    env.jobs.append(job(url='https://jobs.example.com/v'))
    env.scan(baseline=True)
    c = connect(env.db)
    record_review(c, 1, 'VERIFIED_TR', 'me', 'https://careers.example/tr', 'We hire in Türkiye')
    c.execute('UPDATE jobs SET reported_at=NULL, reported_via=NULL')
    c.commit()
    _, text = env.scan()
    assert '**VERIFIED_TR**' in text and 'https://careers.example/tr' in text and 'We hire in Türkiye' in text


def test_legacy_verified_flagged_in_report(env):
    env.jobs.append(job())
    env.scan(baseline=True)
    c = connect(env.db)
    c.execute("UPDATE jobs SET reviewed_status='VERIFIED_TR', reported_at=NULL")
    c.execute("INSERT INTO review_events(job_id,reviewed_at,reviewer,status) VALUES(1,'t','legacy-migration','VERIFIED_TR')")
    c.commit()
    _, text = env.scan()
    assert 'legacy, kanıt yok' in text


def test_applied_job_not_listed_or_marked(env):
    env.jobs.append(job())
    env.scan(baseline=True)
    c = connect(env.db)
    c.execute("INSERT INTO applications(company,title,url,status,applied_at) VALUES('ExampleCo','Senior Python Backend Engineer','','applied','2026-11-06')")
    c.execute('UPDATE jobs SET reported_at=NULL, reported_via=NULL')
    c.commit()
    _, text = env.scan()
    assert '## ExampleCo' not in text
    assert c.execute('SELECT reported_at FROM jobs').fetchone()[0] is None


def test_profile_max_posting_age_days(env, tmp_path):
    (tmp_path / 'profile.yaml').write_text('max_posting_age_days: 30\n', encoding='utf-8')
    env.jobs += [job(source_id='old', title='Old Backend', published_at=iso(100)),
                 job(source_id='new', title='New Backend', published_at=iso(2))]
    _, text = env.scan()
    assert 'New Backend' in text and '## ExampleCo — Old Backend' not in text


def test_review_url_matching_multiple_jobs(env, capsys):
    env.jobs += [job(source_id='1', url='https://jobs.example.com/same'), job(source_id='2', url='https://jobs.example.com/same')]
    env.scan()
    a = Namespace(db=env.db, job_id=None, url='https://jobs.example.com/same', status='UNKNOWN', reviewer='me',
                  evidence_url=None, evidence_quote=None, notes=None)
    assert cli.review(a) == 1
    assert 'multiple' in capsys.readouterr().err
    c = connect(env.db)
    assert c.execute('SELECT count(*) FROM review_events').fetchone()[0] == 0


def test_baseline_with_failing_email(env, monkeypatch):
    env.jobs.append(job())
    def boom(s, b): raise RuntimeError('smtp down')
    monkeypatch.setattr(cli, 'send_email', boom)
    rc, _ = env.scan(baseline=True, email=True)
    assert rc == 1
    c = connect(env.db)
    assert c.execute('SELECT reported_via FROM jobs').fetchone()[0] == 'baseline'
    env.jobs.append(job(source_id='2', title='Backend Engineer', url='https://jobs.example.com/2'))
    rc, _ = env.scan(baseline=True, email=True)
    assert rc == 1
    assert c.execute("SELECT reported_at FROM jobs WHERE source_id='2'").fetchone()[0] is not None


def test_load_env_bom(tmp_path, monkeypatch):
    f = tmp_path / '.env'
    f.write_bytes(b'\xef\xbb\xbfBOM_X=1\n')
    monkeypatch.delenv('BOM_X', raising=False)
    cli.load_env(f)
    try:
        assert os.environ.get('BOM_X') == '1'
    finally:
        os.environ.pop('BOM_X', None)


def test_low_priority_and_policy():
    base = evaluate(job(), PROFILE)['score']
    r = evaluate(job(company='Supabase'), PROFILE)
    assert r['status'] != 'excluded' and r['score'] == base - 20 and 'Düşük öncelik' in r['evidence']
    validate_profile({'exclude_company_policy': 'all_roles'})
    with pytest.raises(ValueError):
        validate_profile({'exclude_company_policy': 'some_roles'})


def test_scan_stops_on_bad_policy(env, tmp_path):
    (tmp_path / 'profile.yaml').write_text('exclude_company_policy: some_roles\n', encoding='utf-8')
    rc, _ = (cli.run_scan(Namespace(db=env.db, profile=str(tmp_path / 'profile.yaml'), sources=str(tmp_path / 'sources.yaml'),
                                    output=str(tmp_path / 'o.md'), email=False, env_file=None, baseline=False, max_age_days=None)), None)
    assert rc == 1


def test_load_env(tmp_path, monkeypatch):
    f = tmp_path / '.env'
    f.write_text('# c\nFOO_X=bar\nQ_X="quoted v"\nKEEP_X=file\n', encoding='utf-8')
    monkeypatch.setenv('KEEP_X', 'shell')
    monkeypatch.delenv('FOO_X', raising=False)
    monkeypatch.delenv('Q_X', raising=False)
    cli.load_env(f)
    try:
        assert os.environ['FOO_X'] == 'bar' and os.environ['Q_X'] == 'quoted v' and os.environ['KEEP_X'] == 'shell'
    finally:
        os.environ.pop('FOO_X', None)
        os.environ.pop('Q_X', None)


def test_smtp_validation(monkeypatch):
    called = []
    monkeypatch.setattr(cli.smtplib, 'SMTP', lambda *a, **k: called.append(1))
    for k in ['SMTP_PASSWORD', 'SMTP_PORT']:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('SMTP_HOST', 'h'); monkeypatch.setenv('SMTP_FROM', 'a@b'); monkeypatch.setenv('REPORT_TO', 'a@b')
    monkeypatch.setenv('SMTP_USER', 'u')
    with pytest.raises(ValueError, match='SMTP_PASSWORD'):
        cli.send_email('s', 'b')
    monkeypatch.setenv('SMTP_PASSWORD', 'p'); monkeypatch.setenv('SMTP_PORT', 'abc')
    with pytest.raises(ValueError, match='SMTP_PORT'):
        cli.send_email('s', 'b')
    assert not called


def test_legacy_migration(tmp_path):
    p = tmp_path / 'old.sqlite3'
    raw = sqlite3.connect(p)
    raw.executescript('''CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
        company TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, location TEXT, description TEXT, published_at TEXT,
        date_kind TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        eligibility_status TEXT NOT NULL DEFAULT 'unknown', eligibility_evidence TEXT, score INTEGER NOT NULL DEFAULT 0,
        technologies_json TEXT NOT NULL DEFAULT '[]', UNIQUE(source, source_id));
        CREATE TABLE review_events (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
        reviewed_at TEXT NOT NULL, reviewer TEXT NOT NULL, status TEXT NOT NULL, evidence_url TEXT, evidence_quote TEXT, notes TEXT);
        INSERT INTO jobs(source,source_id,company,title,url,first_seen_at,last_seen_at,eligibility_status) VALUES
        ('a','1','C','T1','u1','2026-01-01','2026-01-01','VERIFIED_TR'),('a','2','C','T2','u2','2026-01-02','2026-01-02','unknown');''')
    raw.commit(); raw.close()
    for _ in range(2):
        c = connect(p)
        rows = {r['source_id']: r for r in c.execute('SELECT * FROM jobs')}
        assert rows['1']['reported_via'] == 'legacy' and rows['1']['reported_at'] == '2026-01-01'
        assert rows['1']['reviewed_status'] == 'VERIFIED_TR' and rows['2']['reviewed_status'] is None
        assert c.execute('SELECT count(*) FROM review_events WHERE reviewer=?', ('legacy-migration',)).fetchone()[0] == 1
        c.close()
