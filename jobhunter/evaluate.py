"""Conservative heuristics: uncertain eligibility is NEVER verified."""
import re
from functools import lru_cache

from .locations import BUILTIN_LOCATIONS, LOC_RESTRICTED_PREFIX, _phrase_rx, classify_location, region_of

# Abbreviations are case-sensitive (so "join us only" is not a restriction); full names are not.
_GEO = r'(?:(?:US|USA|UK|EU|EEA)\b|U\.S\.|(?i:united states|united kingdom|canada)\b)'
_ABBR = r'(?:US|USA|UK|EU|EEA|U\.S\.)'
NEGATIVE = [
    r'(?<!\w)' + _GEO + r'\s+(?i:only)\b',
    r'(?i:\bmust (?:be )?(?:based|located) in (?:the )?)' + _GEO,
    r'(?i:\bmust be )' + _ABBR + r'-based',
    r'(?<!\w)' + _ABBR + r'-based (?i:candidates?|applicants?|only)\b',
    r'(?i:\bauthori[sz]ed to work in (?:the )?)' + _GEO,
    r'(?i:\bremote (?:only )?(?:within|in) (?:the )?)' + _GEO,
    r'(?i:\b(?:hybrid|on.site|in.office)\s+(?:only|required|role|position)\b)',
]
# No Türkiye exception: a match is a restriction, even in a multi-country list. Such jobs are
# not hidden; they appear (with the matched phrase) in the report's "Elenen ilanlar" section.
NEGATIVE_RE = [re.compile(p) for p in NEGATIVE]
ROLE = re.compile(r'\b(?:backend|back.end|software engineer|platform engineer|python|java|distributed|ai engineer|llms?|genai)\b', re.I)
TECH = re.compile(r'\b(?:java|spring boot|python|fastapi|distributed systems|kafka|microservices|postgresql|llms?|genai|ai platform|kubernetes|docker|aws|redis|rag)\b', re.I)
TITLE_NEGATIVE = re.compile(r'\b(?:front.?end|ios|android|mobile|react native|flutter|designer|ux|ui)\b', re.I)
TITLE_BACKEND = re.compile(r'\b(?:backend|back.end|platform|distributed|python|java|llms?|ai)\b', re.I)
# A stated length only counts when it sits directly next to a contract keyword
# ("3-month contract", "contract of 3 months"); "long-term ... first 3 months" does not.
_KW = r'(?:contract(?:or)?|fixed[- ]term|temporary|FTC)'
CONTRACT_AFTER = re.compile(r'^\s*[-–,]?\s*\(?\s*(?:fixed[- ]term\s+)?' + _KW + r'\b', re.I)
CONTRACT_BEFORE = re.compile(r'(?:' + _KW + r'|(?<![-\w])term)(?:\s+(?:role|position|engagement|assignment|length|duration))*\s*(?:of|is|for|:|-|–|,|\()?\s*(?:approximately|about|around|~)?\s*$', re.I)
CONTRACT_SKIP = re.compile(r'\b(?:probation(?:ary)?|notice|trial|experience)\b', re.I)
SENTENCE_END = re.compile(r'[.;!?\n]')
CONTRACT_NUM = re.compile(r'(\d+)(?:\s*(?:-|–|to)\s*(\d+))?\s*[- ]?(months?|years?|yrs?)\b', re.I)
ALLOWED_POLICIES = (None, 'all_roles')


def validate_profile(profile):
    """Raise ValueError for an invalid profile; return a list of warnings (e.g. deprecated keys)."""
    policy = profile.get('exclude_company_policy')
    if policy not in ALLOWED_POLICIES:
        raise ValueError(f'Unsupported exclude_company_policy {policy!r}; only "all_roles" is implemented')
    warnings = []
    legacy = bool((profile.get('required') or {}).get('worldwide_or_b2b'))
    if 'search' in profile:
        search_config(profile)
        if legacy:
            raise ValueError('profile.search and required.worldwide_or_b2b cannot be used together; remove worldwide_or_b2b')
    elif legacy:
        warnings.append('required.worldwide_or_b2b is deprecated; use the "search:" block (EMEA / remote / B2B-contract profile)')
    return warnings


def contract_months(text):
    """Longest explicitly stated contract length in months, or None. Ranges use the upper bound."""
    found = []
    for m in CONTRACT_NUM.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 30]
        # Probation/notice/trial/experience only matter within the same sentence.
        # Scan a 40-char slice (so "Indus|trial" is never cut into a false "trial"), but only count
        # skip words within 15 chars of the number.
        pre = SENTENCE_END.split(text[max(0, m.start() - 40):m.start()])[-1]
        post = SENTENCE_END.split(text[m.end():m.end() + 40])[0]
        if (any(len(pre) - k.end() <= 15 for k in CONTRACT_SKIP.finditer(pre))
                or any(k.start() <= 15 for k in CONTRACT_SKIP.finditer(post))):
            continue
        if not (CONTRACT_AFTER.search(after) or CONTRACT_BEFORE.search(before)):
            continue
        n = int(m.group(2) or m.group(1))
        found.append(n * (12 if m.group(3).lower().startswith(('y',)) else 1))
    return max(found) if found else None


STATUS_RANK = {'unknown': 0, 'global_unconfirmed': 1, 'candidate_tr': 2}

# Optional gate (profile: required.worldwide_or_b2b): keep only jobs that are remote AND mention a
# worldwide-hiring phrase or a B2B/contractor engagement. "worldwide" / "B2B" alone is too noisy
# ("users worldwide", "B2B SaaS"), so only work-related phrasings count.
NO_SIGNAL_LABEL = 'Worldwide/B2B sinyali yok'
_REMOTE_RX = re.compile(r'\bremote\b|work(?:ing)? from (?:home|anywhere)|home[- ]based|fully distributed', re.I)
_WORLDWIDE_RX = re.compile(
    r"work(?:ing)? from anywhere|anywhere in the world|worldwide remote|remote[\s(,:-]{0,3}(?:worldwide|global(?:ly)?|anywhere)\b"
    r"|(?:worldwide|global|anywhere)\s*\(?\s*remote|hir(?:e|ing) (?:\w+ ){0,2}(?:worldwide|globally|anywhere)|from any country", re.I)
_B2B_RX = re.compile(
    r"\bb2b\b(?!\s*(?:saas|software|product|compan|market|sales|customer|platform|app|business|commerce|brand))"
    r"|independent contractor|contractor (?:agreement|basis|model|arrangement|engagement)|as a contractor|employer of record|\beor\b", re.I)


def worldwide_or_b2b(lr, title, loc, desc):
    """True when the job is remote and mentions worldwide hiring or a B2B/contractor engagement."""
    remote = bool(_REMOTE_RX.search(f'{title} {loc} {desc}'))
    worldwide = lr.outcome == 'open' or bool(_WORLDWIDE_RX.search(f'{loc} {desc}'))
    b2b = bool(_B2B_RX.search(f'{title} {desc}'))
    return remote and (worldwide or b2b)


_TR_REMOTE = re.compile(r'(?:remote.{0,65}(?:turkey|türkiye|turkiye)|(?:turkey|türkiye|turkiye).{0,65}remote)', re.I)
# "Remote - EMEA (excluding Turkey)", "Türkiye not eligible": the pairing excludes Türkiye, it is not a hint for it.
_TR_NEGATION = re.compile(r"\b(?:exclud\w*|except\w*|outside|without|cannot|can't|unable|ineligible"
                          r"|not\s+(?:eligible|open|available|hiring|considered|accepting|possible)"
                          r"|no\s+(?:hiring|candidates|applicants))\b", re.I)


def _tr_remote(text):
    """True when remote and Türkiye are paired in the text without a negation in or right after the pairing."""
    for m in _TR_REMOTE.finditer(text):
        if not _TR_NEGATION.search(m.group(0) + ' ' + text[m.end():m.end() + 30]):
            return True
    return False


def _positive(text, lr, loc):
    """Combine the text-based remote/Türkiye logic with the location result; the better status wins.
    Returns (status, evidence, score bonus)."""
    remote = bool(re.search(r'\bremote\b|work from anywhere|distributed team|work from home', text, re.I))
    # Country mention is NOT sufficient for confirmation; require explicit remote location pairing.
    tr_remote = _tr_remote(text)
    # Avoid using a casual country mention in the description as proof of legal hiring eligibility.
    verified = tr_remote and bool(re.search(r'\b(?:based in|work from|location|eligible|hiring|open to|remote)\b', text, re.I))
    bonuses = [0]
    if verified:
        status, evidence = 'candidate_tr', 'Text indicates remote + Türkiye; manually verify exact hiring terms'
        bonuses.append(25)
    elif remote:
        glob = bool(re.search(r'worldwide|global|anywhere', text))
        status, evidence = 'global_unconfirmed' if glob else 'unknown', 'Remote indicated; Türkiye contracting eligibility not verified'
        bonuses.append(15 if glob else 5)
    else:
        status, evidence = 'unknown', 'Remote and Türkiye eligibility not verified'
    out = lr.outcome
    quoted = f'"{loc}"'
    loc_status, loc_evidence = None, None
    if out == 'turkiye':
        loc_status, loc_evidence = 'candidate_tr', f'Konum Türkiye + remote/Ankara: {quoted}; işe alım koşullarını elle doğrulayın'
        bonuses.append(25)
    elif out == 'open':
        loc_status, loc_evidence = 'global_unconfirmed', f'Konum açık ({", ".join(lr.matched)}): {quoted}; Türkiye işe alım uygunluğu doğrulanmadı'
        bonuses.append(20)
    elif out == 'regional':
        loc_status, loc_evidence = 'global_unconfirmed', f'Bölgesel ({", ".join(lr.matched)}), Türkiye doğrulanmadı: {quoted}'
        bonuses.append(10)
    elif out == 'tr_unknown':
        bonuses.append(10)
    if loc_status and STATUS_RANK[loc_status] >= STATUS_RANK[status]:
        status, evidence = loc_status, loc_evidence
    elif lr.unclassified:
        evidence += f' | Sınıflandırılamayan konum: "{", ".join(lr.unclassified)}"'
    return status, evidence, max(bonuses)


def evaluate(job, profile, locations=None):
    """Heuristic evaluation. With a `search:` block in the profile the search-profile path is used
    (role policy, remote, region, B2B/contract); otherwise the original rules apply, including the
    deprecated required.worldwide_or_b2b gate."""
    title = job['title']
    loc = job.get('location', '') or ''
    desc = job.get('description', '') or ''
    raw = f'{loc} {desc}'
    text = raw.lower()
    title_match = bool(ROLE.search(title))
    matched = sorted(set('llm' if g == 'llms' else g for g in (m.group(0).lower() for m in TECH.finditer(f'{title} {desc}'))))
    exclusions = [s.casefold() for s in profile.get('exclude_companies', [])]
    if job['company'].casefold() in exclusions:
        return {'status':'excluded', 'evidence':'Excluded company', 'score':0, 'tech':matched}
    if 'search' in profile:
        return _evaluate_search(job, profile, locations or BUILTIN_LOCATIONS, matched)
    if not title_match or (TITLE_NEGATIVE.search(title) and not TITLE_BACKEND.search(title)):
        return {'status':'irrelevant', 'evidence':'Role title mismatch', 'score':0, 'tech':matched}
    for rx in NEGATIVE_RE:
        m = rx.search(raw)
        if m:
            return {'status':'ineligible', 'evidence':f'Explicit geographic or on-site restriction: "{m.group(0)}" (review source)', 'score':0, 'tech':matched}
    lr = classify_location(loc, locations or BUILTIN_LOCATIONS)
    if lr.outcome == 'restricted':
        return {'status':'ineligible', 'loc':'restricted', 'score':0, 'tech':matched,
                'evidence':f'{LOC_RESTRICTED_PREFIX}{", ".join(lr.labels)}]: "{loc}" (tüm alternatifler Türkiye dışı veya yerinde/hibrit; kaynağı inceleyin)'}
    minimum = (profile.get('required') or {}).get('minimum_contract_months')
    if minimum:
        months = contract_months(f'{title} {desc}')
        if months is not None and months < minimum:
            return {'status':'ineligible', 'loc':lr.outcome, 'evidence':f'Stated contract length {months} months < required {minimum} months (review source)', 'score':0, 'tech':matched}
    status, evidence, bonus = _positive(text, lr, loc)
    if ((profile.get('required') or {}).get('worldwide_or_b2b') and status != 'candidate_tr'
            and not worldwide_or_b2b(lr, title, loc, desc)):
        # Same prefix as location rejections so the report groups these instead of listing each job.
        return {'status':'ineligible', 'loc':lr.outcome, 'score':0, 'tech':matched,
                'evidence':f'{LOC_RESTRICTED_PREFIX}{NO_SIGNAL_LABEL}]: "{loc}" (remote + worldwide/B2B ifadesi bulunamadı; profile.required.worldwide_or_b2b)'}
    strong = [x.lower() for x in profile.get('technologies', {}).get('strong', [])]
    score = max(0, min(100, 25 + bonus + min(35, 7 * len(set(matched).intersection(strong))) + (10 if re.search(r'senior|staff|lead', title, re.I) else 0)))
    low = [s.casefold() for s in profile.get('low_priority_companies', []) or []]
    if job['company'].casefold() in low:
        score = max(0, score - 20)
        evidence += ' | Düşük öncelik (low_priority_companies)'
    return {'status':status, 'evidence':evidence, 'score':score, 'tech':matched, 'loc':lr.outcome}


# ---- search profile: role policy + remote + region + B2B/contract ------------------------------------
SEARCH_REJECT_PREFIX = 'Search profile ['
SEARCH_KEYS = {'regions', 'remote', 'engagement', 'turkiye_any_engagement', 'roles'}
ROLE_KEYS = ('include', 'exclude', 'exclude_always', 'rescue')
ENGAGEMENT_KINDS = ('b2b', 'contract')

# Defaults: wide scope (plain "Engineer"/"Developer" pass); non-software engineering and non-target
# roles go to exclude_always; "exclude" words are rescued by a backend-ish word in the same title.
DEFAULT_ROLES = {
    'include': ['engineer', 'developer', 'programmer', 'sre', 'devops', 'mlops', 'backend', 'back-end',
                'full-stack', 'fullstack'],
    'exclude': ['frontend', 'front-end', 'ios', 'android', 'mobile', 'react native', 'flutter', 'ui', 'ux',
                'embedded', 'firmware', 'game', 'unity', 'unreal', 'salesforce', 'wordpress', 'shopify'],
    'exclude_always': ['sales', 'presales', 'pre-sales', 'solutions', 'solution', 'support', 'customer', 'field',
                       'mechanical', 'civil', 'electrical', 'electronics', 'chemical', 'structural',
                       'manufacturing', 'process engineer', 'hvac', 'construction', 'hardware', 'qa', 'quality',
                       'test', 'tester', 'testing', 'sdet', 'designer', 'design engineer', 'manager',
                       'management', 'director', 'head of', 'vp', 'vice president', 'recruiter', 'marketing',
                       'account', 'advocate', 'evangelist', 'relations', 'intern', 'internship',
                       'working student', 'werkstudent', 'apprentice', 'trainee'],
    'rescue': ['backend', 'back-end', 'platform', 'distributed', 'python', 'java', 'golang', 'llm', 'ai', 'ml',
               'genai', 'data', 'cloud', 'devops', 'api'],
}

# employment_type field values (lower-case, letters/digits only)
ENGAGEMENT_VALUES = {
    'b2b': {'b2b', 'freelance', 'freelancer', 'independentcontractor', 'contractor'},
    'contract': {'contract', 'contractor', 'contracttohire', 'temporary', 'temp', 'fixedterm'},
}
PERMANENT_VALUES = {'fulltime', 'permanent'}

_NUM = r'\d+(?:\s*(?:-|–|to)\s*\d+)?'
# Contextual phrases over title + description. "B2B SaaS", "smart contract" and bare "contract" do not count.
_ENG_TEXT = [  # (kind, regex)
    ('b2b', re.compile(r'\bb2b[\s-]+(?:contract(?:or)?s?|basis|agreement|engagement|arrangement|cooperation|collaboration)\b', re.I)),
    ('b2b', re.compile(r'\bb2b\s*(?:or|/)\s*(?:uop|employment|permanent|contract)\b|\b(?:uop|permanent|employment contract)\s*(?:or|/)\s*b2b\b', re.I)),
    ('b2b', re.compile(r'\bindependent contractors?\b|\bas an? (?:independent )?contractor\b|\bcontractor\s+(?:agreement|basis|role|position|engagement)\b', re.I)),
    ('b2b', re.compile(r'\bfreelance\s+(?:role|basis|position|engagement|contract)\b', re.I)),
    ('contract', re.compile(r'\b(?:employment |engagement |contract |work )?type\s*[:\-–]\s*(?:contract(?:or)?|b2b|freelance|fixed[- ]term|temporary)\b', re.I)),
    ('contract', re.compile(r'(?<!smart )\bcontract\s+(?:role|position|basis|engagement|opportunity|assignment)\b', re.I)),
    ('contract', re.compile(r'(?<!smart )\bcontract\s*\(\s*' + _NUM + r'\s*(?:months?|years?)', re.I)),
    ('contract', re.compile(r'\b\d+\s*[-–]?\s*(?:months?|years?)\s+(?:fixed[- ]term\s+)?(?:contract\b|(?-i:FTC)\b)', re.I)),
    ('contract', re.compile(r'\bfixed[- ]term\b|\bcontract[- ]to[- ]hire\b', re.I)),
    ('contract', re.compile(r'\bFTC\b')),
]
_ENG_TITLE = [('contract', re.compile(r'\(\s*contract(?:or)?\s*\)', re.I))]
_ENG_NEGATION = re.compile(r"\b(?:no|not|non|never|without)\b|n't\b", re.I)

_AMOUNT = r'(?:[£$€]|\b(?:USD|EUR|GBP)\s?)\s?\d[\d,.]*\s?[kK]?'
_PAY_RX = re.compile(_AMOUNT + r'(?:\s?[-–]\s?(?:[£$€]\s?)?\d[\d,.]*\s?[kK]?)?'
                     r'(?:\s?(?:/|per\s+|a\s+)\s?(?:hr|hour|h|day|d|year|yr|month|mo|annum)\b)?')
_PAY_UNIT = re.compile(r'\d\s?[kK]\b|/\s?(?:hr|hour|h|day|d|year|yr|month|mo)\b|\b(?:per|a)\s+(?:hr|hour|day|year|yr|month|annum)\b')
_PAY_WORD = re.compile(r'salary|compensation|\bpay\b|\brate\b|remuneration|per hour|hourly|\bbudget\b', re.I)


def _str_list(value, name):
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError(f'{name} must be a non-empty list of non-empty strings')
    return [x.strip() for x in value]


def search_config(profile):
    """Validated, defaulted view of profile['search']. Raises ValueError."""
    raw = profile.get('search')
    if not isinstance(raw, dict):
        raise ValueError('profile.search must be a mapping')
    unknown = set(raw) - SEARCH_KEYS
    if unknown:
        raise ValueError('profile.search: unknown key(s): ' + ', '.join(sorted(map(str, unknown))))
    cfg = {'regions': ['EMEA'], 'remote': True, 'engagement': list(ENGAGEMENT_KINDS), 'turkiye_any_engagement': True}
    if 'regions' in raw:
        cfg['regions'] = _str_list(raw['regions'], 'search.regions')
    if 'engagement' in raw:
        if raw['engagement'] == []:
            cfg['engagement'] = []  # `engagement: []` switches the B2B/contract test off (evidence still ranks +10)
        else:
            cfg['engagement'] = _str_list(raw['engagement'], 'search.engagement')
            bad = [x for x in cfg['engagement'] if x not in ENGAGEMENT_KINDS]
            if bad:
                raise ValueError(f'search.engagement: unsupported value(s) {bad}; allowed: b2b, contract')
    for key in ('remote', 'turkiye_any_engagement'):
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ValueError(f'search.{key} must be true or false')
            cfg[key] = raw[key]
    roles = raw.get('roles', {})
    if not isinstance(roles, dict):
        raise ValueError('search.roles must be a mapping')
    unknown = set(roles) - set(ROLE_KEYS)
    if unknown:
        raise ValueError('search.roles: unknown key(s): ' + ', '.join(sorted(map(str, unknown))))
    cfg['roles'] = {k: (_str_list(roles[k], f'search.roles.{k}') if k in roles else list(DEFAULT_ROLES[k])) for k in ROLE_KEYS}
    return cfg


def validate_search(profile, table):
    """Raise ValueError when the search block is invalid or names a region the location table lacks."""
    if 'search' not in profile:
        return
    cfg = search_config(profile)
    for name in cfg['regions']:
        if name.casefold() not in table.regions:
            raise ValueError(f'search.regions: region {name!r} is not defined in the location table (regions section)')


@lru_cache(maxsize=64)
def _words_rx(words):
    return _phrase_rx(list(words))


def role_ok(title, roles):
    """Title policy: needs an include word; exclude_always always rejects; exclude rejects unless rescued."""
    rx = {k: _words_rx(tuple(roles[k])) for k in ROLE_KEYS}
    if not rx['include'].search(title) or rx['exclude_always'].search(title):
        return False
    return not rx['exclude'].search(title) or bool(rx['rescue'].search(title))


def _norm_value(v):
    return re.sub(r'[^a-z0-9]', '', v.lower())


def _snippet(text, m):
    a, b = max(0, m.start() - 30), min(len(text), m.end() + 30)
    return ('…' if a else '') + ' '.join(text[a:b].split()) + ('…' if b < len(text) else '')


def engagement(job, kinds):
    """(matched, evidence, permanent_field): the employment_type field first, then contextual phrases in
    title + description. permanent_field is the raw field value when it says full-time/permanent."""
    raw = job.get('employment_type')
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = [x for x in raw if isinstance(x, str)]
    else:
        values = []
    values = [v for v in values if v.strip()]
    wanted = set().union(*(ENGAGEMENT_VALUES[k] for k in kinds)) if kinds else set()
    for v in values:
        if _norm_value(v) in wanted:
            return True, f'alan: employment_type="{v.strip()}"', None
    permanent = next((v.strip() for v in values if _norm_value(v) in PERMANENT_VALUES), None)
    title, desc = job.get('title') or '', job.get('description') or ''
    for text, patterns in ((title, _ENG_TITLE), (f'{title}\n{desc}', _ENG_TEXT)):
        best = None
        for kind, rx in patterns:
            if kind not in kinds:
                continue
            for m in rx.finditer(text):
                before = re.split(r'[.;!?\n]', text[max(0, m.start() - 25):m.start()])[-1]
                if _ENG_NEGATION.search(before):
                    continue
                if best is None or m.start() < best[0]:
                    best = (m.start(), _snippet(text, m))
                break
        if best:
            return True, f'metin: "{best[1]}"', permanent
    return False, None, permanent


def pay_quote(job):
    """Verbatim pay text from the source (salary field, Ashby 'Compensation:', or an amount with a pay
    unit/keyword); None when the source states none. Never invented, never used for ranking."""
    salary = job.get('salary')
    if isinstance(salary, str) and salary.strip():
        return ' '.join(salary.split())
    desc = job.get('description') or ''
    m = re.search(r'Compensation:\s*(\S.*)$', desc)
    if m and m.group(1).strip():
        return ' '.join(m.group(1).split())
    for text in (job.get('title') or '', desc):
        for m in _PAY_RX.finditer(text):
            near = text[max(0, m.start() - 30):m.end() + 30]
            if _PAY_UNIT.search(m.group(0)) or _PAY_WORD.search(near):
                return m.group(0).strip().rstrip('.,')
    return None


def _negative_geo(match):
    """'onsite' | 'outside' (US/Canada) | 'emea' (UK/EU/EEA) for a NEGATIVE pattern match."""
    if re.search(r'hybrid|on.site|in.office', match, re.I):
        return 'onsite'
    if re.search(r'(?<![A-Za-z])(?:USA|US|U\.S\.)(?![A-Za-z])|(?i:united states|canada)', match):
        return 'outside'
    return 'emea'


_REGION_TEXT = re.compile(r'\bremote[\s(,:/-]{0,3}(?:emea|europe)\b|\b(?:emea|europe)[\s)(,:/-]{0,3}remote\b', re.I)
REGION_BONUS = {'turkiye': 30, 'open': 20, 'regional': 15, 'country': 5}


def _reject(label, loc, why, tech, kind=None):
    return {'status': 'ineligible', 'loc': kind, 'score': 0, 'tech': tech,
            'evidence': f'{SEARCH_REJECT_PREFIX}{label}]: "{loc}" ({why})'}


def _evaluate_search(job, profile, table, tech):
    """Order: role policy > geographic text restriction > remote > region > B2B/contract > contract length."""
    cfg = search_config(profile)
    title = job['title']
    loc = job.get('location', '') or ''
    desc = job.get('description', '') or ''
    if not role_ok(title, cfg['roles']):
        return {'status': 'irrelevant', 'evidence': 'Role title mismatch', 'score': 0, 'tech': tech}
    raw = f'{loc} {desc}'
    quotes = []
    for rx in NEGATIVE_RE:
        for m in rx.finditer(raw):
            if _negative_geo(m.group(0)) != 'emea':
                return {'status': 'ineligible', 'score': 0, 'tech': tech,
                        'evidence': f'Explicit geographic or on-site restriction: "{m.group(0)}" (review source)'}
            quotes.append(m.group(0))
    rr = region_of(loc, table, cfg['regions'])
    if cfg['remote'] and not rr.remote:
        if rr.onsite_only:
            return _reject('On-site/hybrid', loc, 'yalnızca yerinde/hibrit konum; profile.search.remote', tech)
        if not _REMOTE_RX.search(f'{title} {desc}'):
            return _reject('Uzaktan belirtilmemiş', loc, 'remote ifadesi bulunamadı; profile.search.remote', tech)
    kind, labels = rr.kind, list(rr.labels)
    if kind == 'unknown':
        if _WORLDWIDE_RX.search(f'{loc} {desc}'):
            kind, labels = 'open', ['Worldwide']
        elif _REGION_TEXT.search(f'{loc} {desc}'):
            kind, labels = 'regional', ['EMEA/Europe']
    if kind == 'unknown' and quotes:
        kind = 'country'  # location says nothing, the text pins UK/EU: country-based EMEA
    if kind == 'outside':
        return _reject(f'EMEA dışı: {", ".join(labels)}', loc, 'konum bölge dışı; profile.search.regions', tech, kind)
    if kind == 'unknown':
        return _reject('Bölge belirsiz', loc, 'konumdan bölge anlaşılamadı; profile.search.regions', tech, kind)
    if quotes:
        if kind == 'turkiye':
            return {'status': 'ineligible', 'score': 0, 'tech': tech,
                    'evidence': f'Explicit geographic or on-site restriction: "{quotes[0]}" (review source)'}
        kind = 'country'  # "must be based in the UK": still listed, but country-based
    lr = classify_location(loc, table)
    status, pos_evidence, _ = _positive(raw.lower(), lr, loc)
    if kind in ('turkiye', 'country'):
        if status != 'candidate_tr':
            status = 'unknown'
    elif status == 'unknown':
        status = 'global_unconfirmed'
    exempt = kind == 'turkiye' and status == 'candidate_tr' and cfg['turkiye_any_engagement']
    required = bool(cfg['engagement'])  # `engagement: []` = not required; evidence is still looked up for ranking
    matched, eng_evidence, permanent = engagement(job, cfg['engagement'] or ENGAGEMENT_KINDS)
    if required and not matched and not exempt:
        why = f'employment_type="{permanent}"; ' if permanent else ''
        return _reject('B2B/Contract sinyali yok', loc, f'{why}B2B/contract ifadesi bulunamadı; profile.search.engagement', tech, kind)
    minimum = (profile.get('required') or {}).get('minimum_contract_months')
    if minimum:
        months = contract_months(f'{title} {desc}')
        if months is not None and months < minimum:
            return {'status': 'ineligible', 'loc': kind, 'score': 0, 'tech': tech,
                    'evidence': f'Stated contract length {months} months < required {minimum} months (review source)'}
    if kind == 'country':
        note = f'ülke bazlı EMEA ({", ".join(labels) or "metin kısıtı"}), Türkiye\'den uygunluk doğrulanmalı'
        note += ' (B2B/contract kanıtı var)' if matched else ' (B2B/contract ifadesi yok: çoğu zaman ülkede ikamet ister)'
        if quotes:
            note += f' | metin kısıtı: "{quotes[0]}"'
    elif kind == 'regional':
        note = f'EMEA/bölgesel ({", ".join(labels)}); Türkiye işe alım uygunluğu doğrulanmadı'
    elif kind == 'open':
        note = 'Worldwide/Global konum; Türkiye işe alım uygunluğu doğrulanmadı'
    elif status == 'candidate_tr':
        note = 'Türkiye konumu açıkça geçiyor; işe alım koşullarını elle doğrulayın'
    else:
        note = 'Türkiye geçiyor ama remote/uygunluk belirsiz; elle doğrulayın'
    evidence = note + (f' | {pos_evidence}' if status == 'candidate_tr' and kind != 'turkiye' else '')
    strong = [x.lower() for x in profile.get('technologies', {}).get('strong', [])]
    score = (25 + REGION_BONUS[kind] + (10 if matched else 0) + min(35, 7 * len(set(tech).intersection(strong)))
             + (10 if re.search(r'senior|staff|lead', title, re.I) else 0))
    score = max(0, min(100, score))
    low = [s.casefold() for s in profile.get('low_priority_companies', []) or []]
    if job['company'].casefold() in low:
        score = max(0, score - 20)
        evidence += ' | Düşük öncelik (low_priority_companies)'
    return {'status': status, 'evidence': evidence, 'score': score, 'tech': tech, 'loc': kind,
            'search': {'kind': kind, 'labels': labels, 'engagement': eng_evidence, 'pay': pay_quote(job),
                       'note': note, 'section': 'turkiye' if exempt else 'main'}}
