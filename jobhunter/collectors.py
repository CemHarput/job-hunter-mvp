"""Read only publicly accessible ATS job feeds; never apply to jobs."""
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

TIMEOUT = 20


def clean_html(value):
    return BeautifulSoup(unescape(value or ''), 'html.parser').get_text(' ', strip=True)


def iso(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, tz=timezone.utc).isoformat()
    return str(value)


def fetch_greenhouse(client, token, company):
    url = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true'
    response = client.get(url)
    response.raise_for_status()
    for job in response.json().get('jobs', []):
        yield dict(source='greenhouse', source_id=str(job['id']), company=company,
                   title=job.get('title', ''), url=job.get('absolute_url', ''),
                   location=(job.get('location') or {}).get('name', ''),
                   description=clean_html(job.get('content', '')),
                   published_at=iso(job.get('first_published') or job.get('updated_at')),
                   date_kind='first_published' if job.get('first_published') else ('updated_at' if job.get('updated_at') else None))


def fetch_lever(client, site, company):
    url = f'https://api.lever.co/v0/postings/{site}?mode=json'
    response = client.get(url)
    response.raise_for_status()
    for job in response.json():
        cats = job.get('categories') or {}
        description = ' '.join([clean_html(job.get('description', '')),
                                clean_html(job.get('descriptionPlain', '')),
                                clean_html(' '.join(x.get('content', '') for x in job.get('lists', [])))])
        yield dict(source='lever', source_id=str(job['id']), company=company,
                   title=job.get('text', ''), url=job.get('hostedUrl', ''),
                   location=cats.get('location', ''), description=description,
                   published_at=iso(job.get('createdAt')),
                   date_kind='created_at' if job.get('createdAt') else None,
                   employment_type=cats.get('commitment') or None)


def fetch_ashby(client, board, company):
    url = f'https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true'
    response = client.get(url)
    response.raise_for_status()
    for job in response.json().get('jobs', []):
        if job.get('isListed') is False:
            continue
        description = clean_html(job.get('descriptionHtml') or job.get('descriptionPlain') or '')
        comp = job.get('compensation') or {}
        salary = None
        if isinstance(comp, dict):
            description += ' Compensation: ' + str(comp.get('compensationTierSummary') or '')
            salary = str(comp.get('compensationTierSummary') or '').strip() or None
        yield dict(source='ashby', source_id=str(job.get('id') or job.get('jobUrl')), company=company,
                   title=job.get('title', ''), url=job.get('jobUrl') or job.get('applyUrl') or '',
                   location=job.get('location', ''), description=description,
                   published_at=iso(job.get('publishedAt')),
                   date_kind='published_at' if job.get('publishedAt') else None,
                   employment_type=job.get('employmentType') or None, salary=salary)


def valid_job(job):
    url = job.get('url', '')
    p = urlparse(url)
    return bool(job.get('source_id') and job.get('title') and p.scheme == 'https' and p.netloc and p.netloc.lower() not in ('localhost',))


def collect(sources):
    jobs, errors = [], []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={'User-Agent':'JobHunterMVP/0.1 (public ATS feeds)'}) as client:
        for provider, fn, key in [('greenhouse', fetch_greenhouse, 'token'), ('lever', fetch_lever, 'site'), ('ashby', fetch_ashby, 'board')]:
            for entry in sources.get(provider, []):
                try:
                    jobs.extend(j for j in fn(client, entry[key], entry.get('company', entry[key])) if valid_job(j))
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f'{provider}/{entry.get(key, "?")}: {exc}')
    return jobs, errors
