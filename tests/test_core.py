from jobhunter.db import connect, upsert, is_applied
from jobhunter.evaluate import evaluate

PROFILE = {'exclude_companies':['Insider One'], 'technologies':{'strong':['python','fastapi']}}

def job(**kw):
    j = dict(source='ashby',source_id='1',company='ExampleCo',title='Senior Python Backend Engineer',url='https://jobs.ashbyhq.com/example/1',location='Remote worldwide',description='Python FastAPI',published_at=None,date_kind=None)
    j.update(kw)
    return j

def test_worldwide_not_verified():
    assert evaluate(job(),PROFILE)['status']=='global_unconfirmed'

def test_excluded():
    assert evaluate(job(company='Insider One'),PROFILE)['status']=='excluded'

def test_ineligible():
    assert evaluate(job(location='Remote US only'),PROFILE)['status']=='ineligible'

def test_first_seen_and_application(tmp_path):
    c = connect(tmp_path/'jobs.sqlite3')
    j=job(); e=evaluate(j,PROFILE)
    assert upsert(c,j,e) is True
    assert upsert(c,j,e) is False
    assert not is_applied(c,j)
    c.execute("INSERT INTO applications(company,title,url,status,applied_at) VALUES(?,?,?,?,?)",(j['company'],j['title'],j['url'],'applied','2026-11-06'))
    assert is_applied(c,j)
    c.close()
