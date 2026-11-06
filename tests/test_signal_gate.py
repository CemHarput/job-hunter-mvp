from pathlib import Path

import pytest

from jobhunter.evaluate import NO_SIGNAL_LABEL, evaluate
from jobhunter.locations import LOC_RESTRICTED_PREFIX, load_locations

ROOT = Path(__file__).resolve().parent.parent
TABLE, _ = load_locations(ROOT / 'config/locations.yaml')
ON = {'technologies': {'strong': ['python']}, 'required': {'worldwide_or_b2b': True}}
OFF = {'technologies': {'strong': ['python']}}


def job(**kw):
    j = dict(source='ashby', source_id='1', company='ExampleCo', title='Senior Python Backend Engineer',
             url='https://jobs.example.com/1', location='Remote', description='Python FastAPI', published_at=None, date_kind=None)
    j.update(kw)
    return j


def ev(profile=ON, **kw):
    return evaluate(job(**kw), profile, TABLE)


@pytest.mark.parametrize('loc,desc', [
    ('Remote, Global', 'Python'),
    ('Home based - Worldwide', 'Python'),
    ('Remote', 'We hire anywhere in the world. Python'),
    ('Remote', 'This is a fully remote role, work from anywhere. Python'),
    ('Remote', 'You will work as an independent contractor (B2B). Python'),
    ('Remote', 'B2B contract with long term engagement. Python'),
    ('Remote', 'Engaged through our employer of record. Python'),
])
def test_remote_with_worldwide_or_b2b_passes(loc, desc):
    r = ev(location=loc, description=desc)
    assert r['status'] != 'ineligible', r['evidence']


@pytest.mark.parametrize('loc,desc', [
    ('Remote', 'Python'),                                                        # remote but no signal
    ('Europe', 'Fully remote role in Europe. Python'),                           # regional, no signal
    ('Remote-Friendly (Travel-Required) | San Francisco, CA', 'Python'),
    ('Remote', 'Used by hundreds of millions of users worldwide. Python'),       # worldwide about users
    ('Remote', 'We build for brands in gaming and B2B SaaS. Python'),            # B2B about customers
    ('Distributed', 'Python'),
])
def test_no_signal_is_eliminated_and_grouped(loc, desc):
    r = ev(location=loc, description=desc)
    assert r['status'] == 'ineligible'
    assert r['evidence'].startswith(f'{LOC_RESTRICTED_PREFIX}{NO_SIGNAL_LABEL}]')  # report groups on this prefix


def test_signal_without_remote_is_not_enough():
    r = ev(location='Berlin', description='We hire worldwide, B2B contract. Python')
    assert r['status'] == 'ineligible'  # on-site city: eliminated by the location rule or the gate


def test_turkiye_remote_always_passes():
    r = ev(location='Remote, Turkey', description='Python')
    assert r['status'] == 'candidate_tr'


def test_gate_off_by_default():
    r = ev(profile=OFF, location='Remote', description='Python')
    assert r['status'] != 'ineligible'


def test_gate_never_makes_verified():
    for loc in ('Remote, Global', 'Remote'):
        assert ev(location=loc, description='B2B contract. Python')['status'] != 'VERIFIED_TR'


def test_report_groups_gate_rejections(tmp_path, monkeypatch):
    from argparse import Namespace
    from jobhunter import cli
    jobs = [job(source_id=str(i), location='Remote', description='Python', url=f'https://jobs.example.com/{i}') for i in range(5)]
    jobs.append(job(source_id='ok', location='Remote, Global', url='https://jobs.example.com/ok'))
    monkeypatch.setattr(cli, 'collect', lambda sources: (list(jobs), []))
    (tmp_path / 'p.yaml').write_text('required:\n  worldwide_or_b2b: true\n', encoding='utf-8')
    (tmp_path / 's.yaml').write_text('{}', encoding='utf-8')
    out = tmp_path / 'r' / 'latest.md'
    rc = cli.run_scan(Namespace(db=str(tmp_path / 'j.sqlite3'), profile=str(tmp_path / 'p.yaml'), sources=str(tmp_path / 's.yaml'),
                                output=str(out), email=False, env_file=str(tmp_path / 'none'), baseline=False, max_age_days=None,
                                locations=str(ROOT / 'config/locations.yaml')))
    text = out.read_text(encoding='utf-8')
    assert rc == 0 and f'- {NO_SIGNAL_LABEL}: 5 ilan' in text
    assert 'https://jobs.example.com/ok' in text and 'jobs.example.com/3' not in text.split('Elenen ilanlar')[0]
