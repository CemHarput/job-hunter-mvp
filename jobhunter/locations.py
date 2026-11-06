"""Location classification driven by an editable table (config/locations.yaml).

Conservative by design: a job is only called restricted when EVERY alternative location is a
specific non-Türkiye place (or on-site/hybrid without remote). Anything the table cannot
classify stays unknown and is surfaced so the table can be extended. Never produces VERIFIED.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LOC_RESTRICTED_PREFIX = 'Location restricted ['
ONSITE_LABEL = 'On-site/hybrid'
TR_OTHER_LABEL = 'Türkiye (Ankara dışı, yerinde/hibrit)'

REMOTE_WORDS = ['remote-friendly', 'remote', 'home based', 'home-based', 'work from home', 'wfh', 'distributed']
ONSITE_WORDS = ['hybrid', 'in-office', 'in office', 'on-site', 'onsite', 'on site', 'office-based']
IGNORE_WORDS = ['and', 'the', 'of', 'or', 'only', 'based', 'travel', 'required', 'friendly', 'flexible',
                'location', 'locations']

# Higher is better. Only a job whose alternatives are ALL restricted ends up 'restricted'.
RANK = {'turkiye': 7, 'open': 6, 'regional': 5, 'tr_unknown': 4, 'unclassified': 3,
        'remote_bare': 2, 'empty': 1, 'restricted': 0}

SECTIONS = {'version', 'open', 'regional', 'turkiye', 'turkiye_other_cities', 'ignore', 'restricted',
            'us_state_codes', 'regions'}
# Region used by the search profile when the table has no `regions` section.
DEFAULT_REGIONS = {'EMEA': ['EMEA', 'Europe', 'MENA', 'META', 'Middle East', 'Africa']}
_ABBR = re.compile(r'^[A-Z.]{2,5}$')
# " or " splits only in lowercase, so the US state code "OR" ("Portland, OR") is not a separator.
_SPLIT = re.compile(r'\s*[|;/]\s*|\s+or\s+')


def normalize(text):
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('(', ' ').replace(')', ' ')
    return ' '.join(text.split())


def _phrase_rx(words):
    parts = sorted(words, key=len, reverse=True)
    pats = [r'[\s-]+'.join(re.escape(p) for p in re.split(r'[\s-]+', w)) for w in parts]
    return re.compile(r'(?<!\w)(?:' + '|'.join(pats) + r')(?!\w)', re.I)


@dataclass
class LocationTable:
    rx: object = None                      # combined regex for all table terms
    terms: dict = field(default_factory=dict)       # key -> (kind, label); key = ('S', term) | ('I', casefold)
    state_rx: object = None
    ignore_rx: object = None
    cache: dict = field(default_factory=dict)
    restricted_count: int = 0
    regions: dict = field(default_factory=dict)    # casefold region name -> set of casefold labels


@dataclass
class LocationResult:
    outcome: str
    labels: list
    matched: list
    unclassified: list


def _key(term):
    return ('S', term) if _ABBR.match(term) else ('I', term.casefold())


def build_table(data):
    if not isinstance(data, dict):
        raise ValueError('locations file root must be a mapping')
    unknown = set(data) - SECTIONS
    if unknown:
        raise ValueError('unknown key(s): ' + ', '.join(sorted(map(str, unknown))))

    def str_list(name):
        v = data.get(name)
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise ValueError(f'"{name}" must be a list of non-empty strings')
        return [normalize(x) for x in v]

    terms, spec = {}, {}

    def add(term, kind, label):
        key = _key(term)
        if key in terms and terms[key] != (kind, label):
            raise ValueError(f'term "{term}" defined twice with different kind/label: {terms[key]} vs {(kind, label)}')
        terms[key] = (kind, label)
        spec[key] = term

    for kind, name in (('open', 'open'), ('regional', 'regional'), ('turkiye', 'turkiye'), ('other_city', 'turkiye_other_cities')):
        for t in str_list(name):
            add(t, kind, t)
    restricted = data.get('restricted')
    count = 0
    if restricted is not None:
        if not isinstance(restricted, dict):
            raise ValueError('"restricted" must be a mapping of label -> list of aliases')
        for label, aliases in restricted.items():
            if not isinstance(label, str) or not label.strip():
                raise ValueError('"restricted" labels must be non-empty strings')
            if aliases is None:
                aliases = []
            if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
                raise ValueError(f'"restricted.{label}" must be a list of non-empty strings')
            label = normalize(label)
            for a in [label] + [normalize(a) for a in aliases]:
                add(a, 'restricted', label)
            count += 1
    ignore = [w for w in str_list('ignore')]
    codes = data.get('us_state_codes')
    if codes is not None:
        if not isinstance(codes, list) or not all(isinstance(c, str) and re.fullmatch(r'[A-Z]{2}', c) for c in codes):
            raise ValueError('"us_state_codes" must be a list of two-letter uppercase strings')
    valid_labels = {label.casefold() for kind, label in terms.values() if kind in ('open', 'regional', 'restricted')}
    regions_raw = data.get('regions')
    regions = {}
    if regions_raw is None:
        regions = {n.casefold(): {x.casefold() for x in items} for n, items in DEFAULT_REGIONS.items()}
    else:
        if not isinstance(regions_raw, dict) or not regions_raw:
            raise ValueError('"regions" must be a mapping of region name -> list of labels')
        for name, items in regions_raw.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError('"regions" names must be non-empty strings')
            if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
                raise ValueError(f'"regions.{name}" must be a non-empty list of non-empty strings')
            labels = set()
            for x in items:
                n = normalize(x).casefold()
                if n not in valid_labels:
                    raise ValueError(f'"regions.{name}": "{x}" is not an open/regional term or restricted label')
                labels.add(n)
            regions[normalize(name).casefold()] = labels
    t = LocationTable(terms=terms, restricted_count=count, regions=regions)
    if terms:
        ordered = sorted(terms, key=lambda k: len(spec[k]), reverse=True)
        pats = []
        for k in ordered:
            body = r'\s+'.join(re.escape(p) for p in spec[k].split(' '))
            pats.append(body if k[0] == 'S' else f'(?i:{body})')
        t.rx = re.compile(r'(?<!\w)(?:' + '|'.join(pats) + r')(?!\w)')
    if codes:
        t.state_rx = re.compile(r',\s*(' + '|'.join(sorted(set(codes))) + r')(?=\s*(?:,|$))')
    t.ignore_rx = _phrase_rx(IGNORE_WORDS + ignore)
    return t


# Used when config/locations.yaml is missing: no restricted country/city terms, so nothing is
# eliminated by country or city. The on-site/hybrid rule is code-level (ONSITE_WORDS) and still
# applies without a table: bare Hybrid / On-site / In-office jobs are eliminated either way.
BUILTIN_LOCATIONS = build_table({
    'open': ['Worldwide', 'Global', 'Anywhere', 'Work from anywhere'],
    'regional': ['EMEA', 'Europe', 'MENA', 'META', 'Middle East'],
    'turkiye': ['Turkey', 'Türkiye', 'Turkiye', 'Ankara'],
})


def load_locations(path):
    """Return (table, missing). Missing file -> (BUILTIN_LOCATIONS, True); a broken file raises ValueError."""
    p = Path(path)
    if not p.is_file():
        return BUILTIN_LOCATIONS, True
    try:
        data = yaml.safe_load(p.read_text(encoding='utf-8'))
    except yaml.YAMLError as e:
        raise ValueError(f'{p}: invalid YAML: {e}') from None
    try:
        return build_table(data), False
    except ValueError as e:
        raise ValueError(f'{p}: {e}') from None


def _scan_alt(alt, t):
    """Table hits (kind, label, term), remote flag, on-site flag and unrecognised words of one alternative."""
    s = alt
    hits = []  # (kind, label, term)
    if t.state_rx:
        def st(m):
            hits.append(('restricted', 'United States', m.group(1)))
            return ' '
        s = t.state_rx.sub(st, s)
    if t.rx:
        def term(m):
            text = m.group(0)
            kl = t.terms.get(('S', text)) or t.terms.get(('I', text.casefold()))
            if kl:
                hits.append((kl[0], kl[1], text))
            return ' '
        s = t.rx.sub(term, s)
    remote = bool(_REMOTE_RX.search(s))
    s = _REMOTE_RX.sub(' ', s)
    onsite = bool(_ONSITE_RX.search(s))
    s = _ONSITE_RX.sub(' ', s)
    s = t.ignore_rx.sub(' ', s)
    residue = sorted(set(w.lower() for w in re.findall(r'\w+', s)))
    return hits, remote, onsite, residue


def _classify_alt(alt, t):
    hits, remote, onsite, residue = _scan_alt(alt, t)
    labels = sorted({h[1] for h in hits if h[0] in ('restricted', 'open', 'regional')})
    terms = [h[1] for h in hits]
    tr = [h for h in hits if h[0] == 'turkiye']
    other = [h for h in hits if h[0] == 'other_city']
    if residue:
        # Unknown words (e.g. "excluding" in "EMEA (excluding Turkey)") must never be read as a
        # Türkiye or restricted match; surface them instead (footer / `locations --unclassified`).
        return 'unclassified', [], terms, residue
    if tr or other:
        ankara = any(h[1].casefold() == 'ankara' for h in tr)
        if ankara or (remote and (tr or other)):
            return 'turkiye', [], terms, residue
        if other:
            return 'restricted', [TR_OTHER_LABEL], terms, residue
        return 'tr_unknown', [], terms, residue
    if onsite and not remote:
        return 'restricted', labels or [ONSITE_LABEL], terms, residue
    for kind in ('open', 'regional'):
        if any(h[0] == kind for h in hits):
            return kind, [], [h[1] for h in hits if h[0] == kind], residue
    if any(h[0] == 'restricted' for h in hits):
        return 'restricted', labels, terms, residue
    if remote:
        return 'remote_bare', [], [], residue
    return 'empty', [], [], residue


_REMOTE_RX = _phrase_rx(REMOTE_WORDS)
_ONSITE_RX = _phrase_rx(ONSITE_WORDS)


def classify_location(text, table):
    norm = normalize(text)
    cached = table.cache.get(norm)
    if cached is not None:
        return cached
    alts = [a.strip() for a in _SPLIT.split(norm) if a.strip()] or ['']
    results = [_classify_alt(a, table) for a in alts]
    best = max(results, key=lambda r: RANK[r[0]])  # first wins on ties
    residue = sorted({w for r in results for w in r[3]})
    if best[0] == 'restricted':
        labels = sorted({l for r in results for l in r[1]})
        res = LocationResult('restricted', labels, [], residue)
    else:
        res = LocationResult(best[0], [], best[2], residue)
    table.cache[norm] = res
    return res


# ---- region test (search profile) ---------------------------------------------------------------

_FILLER = {'in', 'within', 'across', 'from'}
REGION_RANK = {'turkiye': 5, 'open': 4, 'regional': 3, 'country': 2, 'unknown': 1, 'outside': 0}


@dataclass
class RegionResult:
    kind: str          # turkiye | open | regional | country | unknown | outside
    labels: list
    remote: bool       # some alternative names remote work
    onsite_only: bool  # every alternative is on-site/hybrid without remote


def region_of(text, table, region):
    """Where does `text` sit relative to a named region (str or list of names; see `regions` in the table)?

    The best alternative wins (turkiye > open > regional > country > unknown > outside). On-site/hybrid
    alternatives without remote do not take part. Never decides eligibility; the caller adds the caveats.
    """
    names = [region] if isinstance(region, str) else list(region)
    allowed = set()
    for n in names:
        allowed |= table.regions.get(normalize(n).casefold(), set())
    alts = [a.strip() for a in _SPLIT.split(normalize(text)) if a.strip()] or ['']
    remote_any, onsite_flags, alt_results = False, [], []
    for alt in alts:
        hits, remote, onsite, residue = _scan_alt(alt, table)
        remote_any = remote_any or remote
        if onsite and not remote:
            onsite_flags.append(True)
            continue
        onsite_flags.append(False)
        if [w for w in residue if w not in _FILLER]:
            alt_results.append(('unknown', []))
            continue
        per = []
        for kind, label, _term in hits:
            if kind in ('turkiye', 'other_city'):
                per.append(('turkiye', None))
            elif kind == 'open':
                per.append(('open', label))
            else:  # regional / restricted
                inside = label.casefold() in allowed
                per.append(((('regional' if kind == 'regional' else 'country') if inside else 'outside'), label))
        if not per:
            alt_results.append(('unknown', []))
            continue
        best = max(REGION_RANK[k] for k, _ in per)
        kind = [k for k, _ in per if REGION_RANK[k] == best][0]
        alt_results.append((kind, sorted({l for k, l in per if k == kind and l})))
    onsite_only = bool(onsite_flags) and all(onsite_flags)
    if not alt_results:
        return RegionResult('unknown', [], remote_any, onsite_only)
    best = max(REGION_RANK[k] for k, _ in alt_results)
    kind = [k for k, _ in alt_results if REGION_RANK[k] == best][0]
    labels = sorted({l for k, ls in alt_results if k == kind for l in ls})
    return RegionResult(kind, labels, remote_any, onsite_only)
