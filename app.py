# reference_checker_app.py
# Streamlit app: bulk reference validator (DOI / PubMed / Crossref) with robust splitting & Excel export
# Updates applied (hardened):
# - Tight explicit vs derived DOI scoring (only hard-accept returns ✅ Real)
# - Accent-aware surname matching ("Çe" -> "Ce")
# - Title similarity check (explicit & derived DOI flows)
# - Volume/Issue/Pages parsing and matching (guards against wrong-but-resolving DOIs)
# - doi.org HEAD check (ensures DOI resolves at the resolver, not just Crossref metadata)
# - Invalid DOI handling (if neither Crossref nor doi.org resolve -> never Real)
# - Trailing DOI punctuation stripped at extraction & link building
# - Strict mismatch guard: if DOI in play but BOTH author/title mismatch -> ❌ possible falsification
# - Adds a numeric score (weights) and shows it in the UI table (3rd column)
# - Adds an expandable "How scoring works" section in the UI
# - Distinguish journal-like with URL from generic web sources; attempt Crossref before labeling
# - PMCID support (verify via NCBI for PMC URLs / PMCID tokens)
# - Crossref second-pass search using fielded params (title/author/journal)
# - DOI-from-URL inference registry (Nature, eLife, ScienceDirect PIIs — guarded by doi.org resolution)
# - API etiquette + retry/backoff
# - Clickable DOI/PubMed links via st.data_editor LinkColumn (with fallback)
# - Derived DOI can hard-accept (✅ Real) ONLY if doi.org resolves AND metadata gates pass
# - NEW: leading bullets / list markers stripped before parsing
# - NEW: organisational/report/dataset web references with URLs routed to manual review rather than false

import streamlit as st
import pandas as pd
import re
import urllib.parse
import asyncio
import httpx
import io
from datetime import date
from difflib import SequenceMatcher
import unicodedata
import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
# import psycopg2
# from psycopg2.extras import Json

# =========================
# Configuration
# =========================

TRUSTED_BASE_DOMAINS = {
    # Global / UN / MDBs
    "who.int", "un.org", "unicef.org", "worldbank.org", "oecd.org",
    # US gov
    "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov", "epa.gov", "data.gov",
    # UK
    "gov.uk", "nhs.uk", "digital.nhs.uk", "ukhsa.gov.uk",
    # EU
    "europa.eu", "ecdc.europa.eu",
    # Academia/courseware (non-journal)
    "openstax.org", "open.edu", "coursera.org", "edx.org",
    # Known research resources
    "ukbiobank.ac.uk",
    # Additional likely trusted org/report sources
    "unaids.org", "saude.gov.br", "bvsms.saude.gov.br",
}

ORG_SOURCE_HINTS = {
    "world health organization", "who", "unaids", "ministry", "department",
    "government", "gov", "national", "programme", "program", "report",
    "reports", "repository", "data repository", "dataset", "data",
    "fact sheet", "statistics", "observatory", "health observatory",
    "agency", "organisation", "organization", "office for", "nhs", "ukhsa"
}

MANUAL_REVIEW_LABEL = "🟡 Manual review – web source / report / dataset"

LEADING_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[\u2022\u2023\u25E6\u2043\u2219•◦▪▸►●*\-–—]+|\d+[.)\]]|[A-Za-z][.)\]])\s*",
    flags=re.UNICODE
)

JOURNAL_NORMALIZATION = {
    "sleep med rev": "sleep medicine reviews",
    "nat rev neurosci": "nature reviews neuroscience",
    "psychol bull": "psychological bulletin",
    "am j clin nutr": "american journal of clinical nutrition",
    "british journal of sports medicine": "british journal of sports medicine",
    "bmc pediatrics": "bmc pediatrics",
    "progress in brain research": "progress in brain research",
    "science": "science",
    "sleep": "sleep",
    "patient education and counseling": "patient education and counseling",
    "nature reviews neuroscience": "nature reviews neuroscience",
    "immunogenetics": "immunogenetics",
}

DEFAULT_HEADERS = {
    "User-Agent": "OU-RefChecker/1.0",
    "Accept": "application/json",
}

# =========================
# Helpers & Normalizers
# =========================

async def fetch_pubmed_esearch_by_doi(client, doi_val: str):
    """Returns PubMed ESearch JSON for a DOI query."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {"db": "pubmed", "term": f"{doi_val}[AID]", "retmode": "json"}
    return await get_with_backoff(client, url, params=params, timeout=20)

def parse_esearch_first_pmid(resp_json) -> str:
    try:
        idlist = resp_json.get("esearchresult", {}).get("idlist", [])
        return idlist[0] if idlist else ""
    except Exception:
        return ""

REVIEW_TITLE_PATTERNS = [
    r"\bsystematic review\b",
    r"\bmeta-analys(?:is|es)\b",
    r"\bscoping review\b",
    r"\bumbrella review\b",
    r"\bnarrative review\b",
    r"\bliterature review\b",
    r"\breview of\b",
    r"\breview\b",
]

def infer_review_from_title(title: str) -> bool:
    t = (title or "").lower()
    if not t:
        return False
    for pat in REVIEW_TITLE_PATTERNS:
        if re.search(pat, t):
            if re.search(r"\bpeer review\b", t):
                continue
            return True
    return False

def classify_review_from_pubtypes(pubtypes: list[str]) -> tuple[bool, str]:
    """Returns (is_review, notes)."""
    pts = [p.strip().lower() for p in (pubtypes or []) if p and str(p).strip()]
    if not pts:
        return False, "no pubtypes"

    strong = {"systematic review", "review", "meta-analysis", "scoping review", "umbrella review"}
    if any(p in strong for p in pts):
        return True, "pubtype match"

    if any("review" in p for p in pts):
        return True, "pubtype contains 'review'"

    return False, "pubtypes present but no review label"

def sanitize_prefix(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s + "_") if s else ""

def strip_leading_list_marker(ref: str) -> str:
    if not ref:
        return ref
    return LEADING_LIST_MARKER_RE.sub("", ref).strip()
    
def normalise_broken_url_spacing(text: str) -> str:
    if not text:
        return text

    fixed_lines = []
    for line in re.split(r"\r?\n", text):
        # fix spaces immediately after a hyphen inside a URL on the same line
        line = re.sub(r"(https?://\S*?)-[ \t]+(\S+)", r"\1-\2", line)

        # collapse only spaces/tabs inside a URL, never across lines
        def _fix_url(match):
            url = match.group(0)
            url = re.sub(r"[ \t]+", "", url)
            return url

        line = re.sub(r"https?://[^\s<>\]]+(?:[ \t]+[^\s<>\]]+)*", _fix_url, line, flags=re.I)
        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def clean_ref(ref: str) -> str:
    s = ref or ""
    s = strip_leading_list_marker(s)
    s = normalise_broken_url_spacing(s)
    s = s.replace("\u00a0", " ")

    s = re.sub(r"\[(?:Online|online)\]", "", s, flags=re.I)
    s = re.sub(r"\bAvailable at\b.*?$", "", s, flags=re.I)
    s = re.sub(r"\bAccessed(?: date)?\b.*?$", "", s, flags=re.I)
    s = " ".join(s.split())
    return s.strip(" .")

def extract_first_author(ref: str) -> str:
    s = ref.lstrip(" \"'“”‘’")
    m = re.match(r"^([A-ZÀ-ÖØ-Þ][^,]{0,120}),", s, flags=re.UNICODE)
    if m:
        return m.group(1).strip()
    m2 = re.match(r"^([A-ZÀ-ÖØ-Þ][^\s,]{0,60})", s, flags=re.UNICODE)
    return m2.group(1) if m2 else ""

def extract_year(ref: str) -> str:
    m = re.search(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", ref)
    return m.group(0) if m else ""

def normalize_journal_name(name: str) -> str:
    name = (name or "").strip().lower()
    key = re.sub(r"[^\w\s]", "", name)
    key = re.sub(r"\s+", " ", key)
    if key in JOURNAL_NORMALIZATION:
        return JOURNAL_NORMALIZATION[key]
    for abbr, full in JOURNAL_NORMALIZATION.items():
        if key.startswith(abbr):
            return full
    return name

def extract_journal(ref: str) -> str:
    s = re.sub(r"doi:.*", "", ref, flags=re.I)
    s = re.sub(r"pmid:.*", "", s, flags=re.I)
    s = re.sub(r"available at:.*", "", s, flags=re.I)
    yr = re.search(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", s)
    if not yr:
        return ""
    after = s[yr.end():].strip()
    parts = re.split(r"[.,]", after)
    for i, part in enumerate(parts):
        if re.search(r"\d", part):
            if i > 0:
                cand = parts[i - 1].strip(" ;:()")
                if len(cand.split()) > 1:
                    return normalize_journal_name(cand)
    for part in parts:
        if any(w in part.lower() for w in [
            "journal", "review", "bulletin", "research", "studies",
            "letters", "proceedings", "transactions", "reports", "pediatrics",
            "neuroscience", "medicine", "science", "sleep", "counseling", "counselling", "education",
            "immunogenetics", "nature reviews", "lancet"
        ]):
            return normalize_journal_name(part.strip(" ;:()"))
    return ""

def extract_title(ref: str) -> str:
    if not ref:
        return ""

    m_q = re.search(r"[\'‘’]\s*([^\'‘’]{2,300}?)\s*[\'‘’]", ref)
    if m_q:
        return m_q.group(1).strip()

    m = re.search(
        r"(?:\(\d{4}[a-z]?\)|[, ]\s*\d{4}[a-z]?)\.?\s+([^\.]{5,300})\.",
        ref
    )
    if m:
        return m.group(1).strip()

    m2 = re.search(
        r"(?:\(\d{4}[a-z]?\)|[, ]\s*\d{4}[a-z]?)\.?\s+(.+?)(?=(?:\.\s+[A-Z][A-Za-z&\-\s]+,|\.\s+pp\.|Available at|https?://|doi:|PMID:|PMCID:|$))",
        ref
    )
    return m2.group(1).strip() if m2 else ""

def extract_doi(ref: str):
    m = re.search(r"\[(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\]\(https?://doi\.org/\1\)", ref)
    if m:
        return m.group(1).rstrip(".,;:)")
    m = re.search(r"\[(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\]", ref)
    if m:
        return m.group(1).rstrip(".,;:)")
    m = re.search(r"doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", ref)
    if m:
        return m.group(1).rstrip(".,;:)")
    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", ref)
    if m:
        return m.group(1).rstrip(".,;:)")
    return None

def extract_pmcid(ref: str):
    m = re.search(r"\bPMCID:\s*(PMC\d+)\b", ref, flags=re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"pmc\.ncbi\.nlm\.nih\.gov\/articles\/(PMC\d+)", ref, flags=re.I)
    if m:
        return m.group(1).upper()
    return ""

def extract_urls(ref: str):
    if not ref:
        return []
    s = normalise_broken_url_spacing(ref)
    urls = re.findall(r"https?://[^\s<>\]]+", s, flags=re.I)
    out = []
    seen = set()
    for u in urls:
        u = u.rstrip(").,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out
def extract_primary_url(ref: str) -> str:
    urls = extract_urls(ref)
    return urls[0] if urls else ""
    
def extract_domains(ref: str):
    urls = extract_urls(ref)
    seen, out = set(), []
    for u in urls:
        try:
            d = urllib.parse.urlparse(u).netloc.lower().replace("www.", "")
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        except Exception:
            pass
    return out

def domain_is_trusted(domain: str) -> bool:
    domain = (domain or "").lower()
    return any(domain == b or domain.endswith("." + b) for b in TRUSTED_BASE_DOMAINS)

def similar(a, b):
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

def journals_match(ref_journal, meta_journal):
    ref_journal = normalize_journal_name(ref_journal)
    meta_journal = normalize_journal_name(meta_journal)
    ref_journal = re.sub(r"[^\w\s]", "", (ref_journal or "").lower())
    meta_journal = re.sub(r"[^\w\s]", "", (meta_journal or "").lower())
    if not ref_journal or not meta_journal:
        return False
    if ref_journal in meta_journal or meta_journal in ref_journal:
        return True
    return similar(meta_journal, ref_journal) > 0.6

def has_bibliographic_shape(ref: str) -> bool:
    if not re.search(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", ref):
        return False
    pages = re.search(r"\bpp?\.\s*\d+", ref, flags=re.I)
    vol_issue = re.search(r"\b\d+\s*\(\d+\)", ref)
    page_range = re.search(r"\b\d+\s*[-–]\s*\d+\b", ref)
    return bool(pages or vol_issue or page_range)

def looks_scholarly_like(ref_clean: str) -> bool:
    yr = bool(extract_year(ref_clean))
    jnl = bool(extract_journal(ref_clean))
    shape = has_bibliographic_shape(ref_clean)
    vol_issue = re.search(r"\b\d{1,4}\s*\(\s*[A-Za-z0-9\-]+\s*\)", ref_clean) is not None
    pages = re.search(r"\bpp?\.\s*\d+", ref_clean, flags=re.I) is not None or re.search(r"\b\d+\s*[-–]\s*\d+\b", ref_clean) is not None
    title_len = len(extract_title(ref_clean) or "")
    return yr and (jnl or shape or vol_issue or pages or title_len >= 25)
    
def looks_like_org_web_source(ref: str, primary_domain: str = "") -> bool:
    ref_l = (ref or "").lower()
    ref_clean = clean_ref(ref)

    hint_hit = any(h in ref_l for h in ORG_SOURCE_HINTS)
    trusted_hit = bool(primary_domain and domain_is_trusted(primary_domain))
    has_url = bool(extract_urls(ref))

    has_identifier = bool(
        extract_doi(ref)
        or re.search(r"\bPMID:\s*\d+\b", ref, flags=re.I)
        or extract_pmcid(ref)
    )

    ref_journal = extract_journal(ref_clean)
    ref_vol, ref_issue, ref_pages, _, _ = extract_vol_issue_pages(ref_clean)

    clearly_journal_structured = bool(
        ref_journal and (ref_vol or ref_issue or ref_pages or has_bibliographic_shape(ref_clean))
    )

    return bool(
        has_url
        and not has_identifier
        and (hint_hit or trusted_hit)
        and not clearly_journal_structured
    )

def strip_accents(s: str) -> str:
    if not s:
        return s
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def surname_match(first_author: str, family: str) -> bool:
    if not first_author or not family:
        return False
    s = strip_accents(first_author.split()[0]).lower()
    f = strip_accents(family).lower()
    return f.startswith(s[:5]) or similar(s, f) >= 0.8

def title_similarity(a: str, b: str) -> float:
    a2 = re.sub(r"\s+", " ", (a or "").strip())
    b2 = re.sub(r"\s+", " ", (b or "").strip())
    return similar(a2, b2)

# --- volume/issue/pages parsing & comparison ---

def extract_vol_issue_pages(ref: str):
    s = ref
    m_vi = re.search(r"\b(\d{1,4})\s*\(\s*([A-Za-z0-9\-]+)\s*\)", s)
    vol = m_vi.group(1) if m_vi else ""
    issue = m_vi.group(2) if m_vi else ""

    m_pp = re.search(r"\bpp?\.\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)", s, flags=re.I)
    if not m_pp:
        m_pp = re.search(r"\b\d{1,4}\s*\(\s*[A-Za-z0-9\-]+\s*\)\s*[,:]?\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)", s)
    if not m_pp:
        m_pp = re.search(r"\b([0-9]+(?:\s*[-–]\s*[0-9]+))\b", s)

    pages = m_pp.group(1) if m_pp else ""

    def parse_pages(pg: str):
        if not pg:
            return "", ""
        parts = re.split(r"[-–]\s*", pg.strip())
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], ""

    p_start, p_end = parse_pages(pages)
    return vol, issue, pages, p_start, p_end

def pages_match(ref_start: str, ref_end: str, cr_pages: str) -> bool:
    cr_start, cr_end = "", ""
    if cr_pages:
        parts = re.split(r"[-–]\s*", str(cr_pages))
        if len(parts) >= 1:
            cr_start = parts[0].strip()
        if len(parts) >= 2:
            cr_end = parts[1].strip()

    if not ref_start and not ref_end:
        return False
    if not cr_start and not cr_end:
        return False

    try:
        rs = int(ref_start) if ref_start else None
        re_ = int(ref_end) if ref_end else rs
        cs = int(cr_start) if cr_start else None
        ce = int(cr_end) if cr_end else cs
        if rs is None or cs is None:
            return False
        if rs == cs:
            return True
        if re_ is not None and ce is not None:
            return not (re_ < cs or ce < rs)
        return False
    except Exception:
        if ref_start and cr_start and ref_start == cr_start:
            return True
        rng = f"{ref_start}-{ref_end}".strip("-")
        return bool(rng and cr_pages and rng in str(cr_pages))

# --- DOI-from-URL inference registry (guarded by doi.org HEAD) ---

def infer_doi_from_url_candidate(url: str) -> str:
    try:
        u = urllib.parse.urlparse(url)
        host = (u.netloc or "").lower()
        path = (u.path or "")

        if host.endswith("nature.com"):
            m = re.search(r"/articles/([a-z0-9\.\-]+)$", path, flags=re.I)
            if m:
                slug = m.group(1)
                if re.match(r"^[a-z]+\.\d{4}\.\d+$", slug, flags=re.I) or re.match(r"^s\d{5,}[-\w]+$", slug, flags=re.I):
                    return f"10.1038/{slug}"

        if "elifesciences.org" in host:
            m = re.search(r"/articles/(\d+)", path)
            if m:
                return f"10.7554/eLife.{m.group(1)}"

        if "sciencedirect.com" in host and "/science/article/pii/" in path:
            m = re.search(r"/pii/([A-Z0-9]+)", path)
            if m:
                pii = m.group(1)
                if re.match(r"^S\d{15,}$", pii):
                    return f"10.1016/{pii}"

        return ""
    except Exception:
        return ""

# =========================
# Reference Splitting
# =========================
def split_references(text: str):
    if not text:
        return []

    text = normalise_broken_url_spacing(text)
    lines = [strip_leading_list_marker(ln.strip()) for ln in re.split(r"\r?\n", text)]

    year_any = re.compile(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", re.I)

    author_or_org_comma = re.compile(r"^[A-ZÀ-ÖØ-Þ][^,]{0,160},", re.UNICODE)
    entity_paren_year = re.compile(
        r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9&'`\-()./ ]+\(\s*(?:1[89]\d{2}|20\d{2})[a-z]?\s*\)",
        re.UNICODE
    )
    entity_comma_year = re.compile(
        r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ&'`\-()./ ]+,\s*(?:1[89]\d{2}|20\d{2})[a-z]?\b",
        re.UNICODE
    )

    noise_line = re.compile(r"^(Available at|Accessed|\[Online\]|Online\.?)", re.I)

    def looks_like_start(line: str) -> bool:
        if not line:
            return False
        if noise_line.match(line):
            return False
        return bool(
            (author_or_org_comma.match(line) and year_any.search(line))
            or entity_paren_year.match(line)
            or entity_comma_year.match(line)
        )

    refs = []
    current = []

    for line in lines:
        if not line:
            if current:
                refs.append(" ".join(current).strip())
                current = []
            continue

        if looks_like_start(line):
            if current:
                refs.append(" ".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:
        refs.append(" ".join(current).strip())

    refs = [" ".join(r.split()) for r in refs if r.strip()]
    return refs

# =========================
# Scoring / Labels
# =========================

def compute_weighted_score(*, doi_explicit_ok, doi_derived_ok, author_ok, title_ok,
                           year_ok, journal_ok, vol_ok, issue_ok, pages_ok, shape_ok) -> int:
    score = 0
    if doi_explicit_ok: score += 4
    if doi_derived_ok:  score += 2
    if author_ok:       score += 2
    if title_ok:        score += 2
    if year_ok:         score += 2
    if journal_ok:      score += 1
    if vol_ok:          score += 1
    if issue_ok:        score += 1
    if pages_ok:        score += 1
    if shape_ok:        score += 1
    return score

def score_result(
    year_ok, author_ok, journal_ok, *,
    doi_explicit_ok=False, doi_derived_ok=False,
    ref="", shape_ok=False, has_url=False, had_crossref_candidate=False,
    title_ok=False, vol_ok=False, issue_ok=False, pages_ok=False,
    doi_invalid=False, manual_review_web=False
):
    weighted_score = compute_weighted_score(
        doi_explicit_ok=doi_explicit_ok, doi_derived_ok=doi_derived_ok,
        author_ok=author_ok, title_ok=title_ok, year_ok=year_ok, journal_ok=journal_ok,
        vol_ok=vol_ok, issue_ok=issue_ok, pages_ok=pages_ok, shape_ok=shape_ok
    )

    if manual_review_web:
        return MANUAL_REVIEW_LABEL, "organisational/report/dataset-style web source with URL", weighted_score

    if doi_invalid:
        minimally_ok = any([year_ok, author_ok, title_ok, journal_ok, vol_ok, issue_ok, pages_ok]) or shape_ok
        label = "⚠ Suspicious" if minimally_ok else "❌ possible falsification"
        return label, "invalid DOI", weighted_score

    if (doi_explicit_ok or doi_derived_ok) and (not author_ok) and (not title_ok):
        return (
            "❌ possible falsification",
            "DOI resolves but both first-author and title mismatch (likely wrong DOI or fabricated pairing)",
            weighted_score
        )

    if doi_explicit_ok or doi_derived_ok:
        id_match = title_ok
        biblio_one = any([year_ok, journal_ok, vol_ok, issue_ok, pages_ok])
        if id_match and biblio_one:
            return "✅ Real", "", weighted_score

    if (not doi_explicit_ok and not doi_derived_ok) and year_ok and author_ok and journal_ok:
        return "✅ Real", "", weighted_score

    reasons = []
    if not (doi_explicit_ok or doi_derived_ok): reasons.append("no DOI")
    if not author_ok: reasons.append("author mismatch")
    if not title_ok: reasons.append("title mismatch")
    if not year_ok: reasons.append("year mismatch")
    if not journal_ok: reasons.append("journal mismatch")
    if not vol_ok: reasons.append("volume mismatch")
    if not issue_ok: reasons.append("issue mismatch")
    if not pages_ok: reasons.append("pages mismatch")

    if weighted_score >= 4:
        return "⚠ Suspicious", ", ".join(reasons), weighted_score

    title_len = len(extract_title(ref) or "")
    has_author_token = bool(extract_first_author(ref))
    if (not has_url) and (not had_crossref_candidate):
        if shape_ok and has_author_token and title_len >= 25:
            return "⚠ Suspicious", ", ".join(reasons) or "unverifiable but well-formed", weighted_score
        else:
            return "❌ possible falsification", ", ".join(reasons) or "failed most checks", weighted_score
    else:
        if shape_ok and has_author_token and title_len >= 35:
            return "⚠ Suspicious", ", ".join(reasons), weighted_score
        return "❌ possible falsification", ", ".join(reasons) or "failed most checks", weighted_score

# =========================
# Async API Calls
# =========================

async def fetch_pubmed_efetch_xml(client, pmid_val: str):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": pmid_val, "retmode": "xml"}
    return await get_with_backoff(client, url, params=params, timeout=20)

def parse_pubmed_pubtypes(xml_text: str) -> list[str]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
        out = []
        for pt in root.findall(".//PublicationTypeList/PublicationType"):
            if pt.text:
                out.append(pt.text.strip())
        return out
    except Exception:
        return []

async def get_with_backoff(client, url, **kwargs):
    last = None
    for attempt in range(3):
        resp = await client.get(url, **kwargs)
        last = resp
        if resp.status_code in (429, 503):
            await asyncio.sleep(2 ** attempt)
            continue
        return resp
    return last

async def fetch_crossref_doi(client, doi_val):
    doi_val = doi_val.rstrip(".,;:)")
    safe_doi = urllib.parse.quote(doi_val)
    url = f"https://api.crossref.org/works/{safe_doi}"
    return await get_with_backoff(client, url, timeout=20)

async def fetch_pubmed(client, pmid_val):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "pubmed", "id": pmid_val, "retmode": "json"}
    return await get_with_backoff(client, url, params=params, timeout=20)

async def fetch_pmc(client, pmcid_val):
    pmcid_num = re.sub(r"^PMC", "", pmcid_val, flags=re.I)
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "pmc", "id": pmcid_num, "retmode": "json"}
    return await get_with_backoff(client, url, params=params, timeout=20)

async def fetch_crossref_bib(client, ref_text):
    url = "https://api.crossref.org/works"
    params = {"query.bibliographic": ref_text, "rows": 5}
    return await get_with_backoff(client, url, params=params, timeout=20)

async def fetch_crossref_fielded(client, *, title, author_surname, container_title):
    url = "https://api.crossref.org/works"
    params = {
        "query.title": title or "",
        "query.author": author_surname or "",
        "query.container-title": container_title or "",
        "rows": 5
    }
    return await get_with_backoff(client, url, params=params, timeout=20)

async def check_doi_org_head(client, doi_val: str) -> bool:
    url = f"https://doi.org/{doi_val.rstrip('.,;:)')}"
    try:
        resp = await client.head(url, timeout=15, follow_redirects=False)
        return resp.status_code in (200, 301, 302, 303, 307, 308)
    except Exception:
        return False

# =========================
# Strict Crossref chooser
# =========================

def choose_best_crossref_item(ref, items, first_author, year, ref_journal, title):
    def get_year(it):
        for path in (("issued", "date-parts"), ("published-print", "date-parts"), ("created", "date-parts")):
            obj = it
            for key in path:
                obj = obj.get(key, {})
            try:
                y = obj[0][0]
                if isinstance(y, int):
                    return y
            except Exception:
                pass
        return None

    def get_title(it):
        if "title" in it and it["title"]:
            return it["title"][0]
        return ""

    best = None
    best_score = -1
    reason = "no acceptable match"

    try:
        y_ref = int(year) if year else None
    except Exception:
        y_ref = None

    if y_ref is not None and y_ref <= 1990:
        tol = 1
        title_threshold = 0.80
    else:
        tol = 2
        title_threshold = 0.75

    for it in items[:5]:
        it_title = get_title(it)
        it_year = get_year(it)
        it_jnl = (it.get("container-title") or [""])[0]
        fams = [a.get("family", "") for a in it.get("author", [])]

        title_sim = similar(title, it_title) if (title and it_title) else 0.0
        year_ok = bool(y_ref is not None and it_year is not None and abs(it_year - y_ref) <= tol)
        auth_ok = bool(first_author and any(surname_match(first_author, f) for f in fams))
        jnl_ok = journals_match(ref_journal, it_jnl)

        passes = (title_sim >= title_threshold and year_ok) or (auth_ok and year_ok)
        score = (title_sim * 0.7) + (0.2 if auth_ok else 0) + (0.1 if jnl_ok else 0)

        if passes and score > best_score:
            best, best_score = it, score
            reason = f"title={title_sim:.2f}, year_ok={year_ok}, auth_ok={auth_ok}, jnl_ok={jnl_ok}, tol={tol}"

    return best, reason

# =========================
# Validation
# =========================
async def validate_single_ref(client, ref, debug_mode=False, check_reviews: bool = False):
    is_review = False
    review_source = ""
    review_notes = ""

    ref = strip_leading_list_marker(ref or "")
    ref = normalise_broken_url_spacing(ref)

    ref_clean = clean_ref(ref)
    first_author = extract_first_author(ref_clean)
    year = extract_year(ref_clean)
    ref_journal = extract_journal(ref_clean).lower()
    title = extract_title(ref_clean)
    doi_val = extract_doi(ref)
    pmid_match = re.search(r"\bPMID:\s*(\d+)\b", ref, flags=re.I)
    pmcid_val = extract_pmcid(ref)
    shape_ok = has_bibliographic_shape(ref_clean)

    domains = extract_domains(ref)
    urls = extract_urls(ref)
    source_url = urls[0] if urls else ""
    primary_domain = domains[0] if domains else ""
    domains_joined = ", ".join(domains)

    trusted_web = bool(primary_domain and domain_is_trusted(primary_domain))
    manual_review_web = looks_like_org_web_source(ref, primary_domain=primary_domain)

    ref_vol, ref_issue, ref_pages, ref_pstart, ref_pend = extract_vol_issue_pages(ref_clean)

    crossref_doi = crossref_journal = crossref_year = ""
    pubmed_id = pubmed_journal = pubmed_year = ""

    doi_ok = year_ok = author_ok = journal_ok = False
    title_ok = False
    vol_ok = issue_ok = pages_ok = False

    doi_source = ""
    had_crossref_candidate = False
    doi_explicit_ok = False
    doi_derived_ok = False
    doi_invalid = False

    if not doi_val and urls:
        for u in urls:
            cand = infer_doi_from_url_candidate(u)
            if cand:
                doi_val = cand
                doi_source = "heuristic"
                break

    scholarly_like = looks_scholarly_like(ref_clean)

    if manual_review_web:
        return {
            "Reference": ref,
            "Extracted DOI": "",
            "DOI Source": "",
            "Crossref DOI": "",
            "Crossref Journal": "",
            "Crossref Year": "",
            "PubMed ID": "",
            "PubMed Journal": "",
            "PubMed Year": "",
            "Score": 0,
            "Source URL": source_url,
            "doi_ok": False,
            "year_ok": bool(year),
            "author_ok": bool(first_author),
            "journal_ok": False,
            "Validation Result": MANUAL_REVIEW_LABEL,
            "Raw Result": MANUAL_REVIEW_LABEL,
            "Notes": "URL detected; organisational/report/dataset-style source; manual review recommended",
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": False,
            "doi_derived_ok": False,
            "Is Review": False,
            "Review Source": "",
            "Review Notes": "",
        }

    if not (doi_val or pmid_match or pmcid_val) and primary_domain and not scholarly_like:
        label = "📄 Web Source (trusted)" if trusted_web else "📄 Web Source"
        return {
            "Reference": ref,
            "Extracted DOI": "",
            "DOI Source": "",
            "Crossref DOI": "",
            "Crossref Journal": "",
            "Crossref Year": "",
            "PubMed ID": "",
            "PubMed Journal": "",
            "PubMed Year": "",
            "Score": 0,
            "Source URL": source_url,
            "doi_ok": False,
            "year_ok": bool(year),
            "author_ok": bool(first_author),
            "journal_ok": False,
            "Validation Result": label,
            "Raw Result": label,
            "Notes": "URL detected; treated as non-academic web source",
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": False,
            "doi_derived_ok": False,
            "Is Review": False,
            "Review Source": "",
            "Review Notes": "",
        }


    ref_clean = clean_ref(ref)
    first_author = extract_first_author(ref_clean)
    year = extract_year(ref_clean)
    ref_journal = extract_journal(ref_clean).lower()
    title = extract_title(ref_clean)
    doi_val = extract_doi(ref)
    pmid_match = re.search(r"\bPMID:\s*(\d+)\b", ref, flags=re.I)
    pmcid_val = extract_pmcid(ref)
    shape_ok = has_bibliographic_shape(ref_clean)

    ref_vol, ref_issue, ref_pages, ref_pstart, ref_pend = extract_vol_issue_pages(ref_clean)

    crossref_doi = crossref_journal = crossref_year = ""
    pubmed_id = pubmed_journal = pubmed_year = ""

    doi_ok = year_ok = author_ok = journal_ok = False
    title_ok = False
    vol_ok = issue_ok = pages_ok = False

    doi_source = ""
    had_crossref_candidate = False
    doi_explicit_ok = False
    doi_derived_ok = False
    doi_invalid = False

    if not doi_val and urls:
        for u in urls:
            cand = infer_doi_from_url_candidate(u)
            if cand:
                doi_val = cand
                doi_source = "heuristic"
                break

    scholarly_like = looks_scholarly_like(ref_clean)

    if manual_review_web:
        return {
            "Reference": ref,
            "Extracted DOI": "",
            "DOI Source": "",
            "Crossref DOI": "",
            "Crossref Journal": "",
            "Crossref Year": "",
            "PubMed ID": "",
            "PubMed Journal": "",
            "PubMed Year": "",
            "Score": 0,
            "Source URL": source_url,
            "doi_ok": False,
            "year_ok": bool(year),
            "author_ok": bool(first_author),
            "journal_ok": False,
            "Validation Result": MANUAL_REVIEW_LABEL,
            "Raw Result": MANUAL_REVIEW_LABEL,
            "Notes": "URL detected; organisational/report/dataset-style source; manual review recommended",
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": False,
            "doi_derived_ok": False,
            "Is Review": False,
            "Review Source": "",
            "Review Notes": "",
        }

    if not (doi_val or pmid_match or pmcid_val) and primary_domain and not scholarly_like:
        label = "📄 Web Source (trusted)" if trusted_web else "📄 Web Source"
        return {
            "Reference": ref,
            "Extracted DOI": "",
            "DOI Source": "",
            "Crossref DOI": "",
            "Crossref Journal": "",
            "Crossref Year": "",
            "PubMed ID": "",
            "PubMed Journal": "",
            "PubMed Year": "",
            "Score": 0,
            "Source URL": source_url,
            "doi_ok": False,
            "year_ok": bool(year),
            "author_ok": bool(first_author),
            "journal_ok": False,
            "Validation Result": label,
            "Raw Result": label,
            "Notes": "URL detected; treated as non-academic web source",
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": False,
            "doi_derived_ok": False,
            "Is Review": False,
            "Review Source": "",
            "Review Notes": "",
        }

    try:
        if doi_val:
            resp = await fetch_crossref_doi(client, doi_val)
            doi_org_ok = await check_doi_org_head(client, doi_val)

            item = {}
            meta_authors = []
            it_title = ""

            if resp.status_code == 200:
                item = resp.json().get("message", {}) or {}
                crossref_doi = doi_val.rstrip(".,;:)")
                crossref_journal = (item.get("container-title") or [""])[0]
                cr_year = (
                    item.get("issued", {}).get("date-parts", [[None]])[0][0]
                    or item.get("created", {}).get("date-parts", [[None]])[0][0]
                )
                crossref_year = str(cr_year or "")
                meta_authors = [a.get("family", "") for a in item.get("author", [])]
                it_title = (item.get("title") or [""])[0]

            doi_ok = (resp.status_code == 200)

            if check_reviews:
                if not is_review:
                    if infer_review_from_title(it_title) or infer_review_from_title(title):
                        is_review = True
                        review_source = "Crossref/title heuristic"
                        review_notes = "title pattern matched"

                if not is_review:
                    pmid_via_doi = ""
                    try:
                        es = await fetch_pubmed_esearch_by_doi(client, doi_val.rstrip(".,;:)"))
                        if es and es.status_code == 200:
                            pmid_via_doi = parse_esearch_first_pmid(es.json())
                    except Exception:
                        pmid_via_doi = ""

                    if pmid_via_doi:
                        resp_xml = await fetch_pubmed_efetch_xml(client, pmid_via_doi)
                        if resp_xml.status_code == 200:
                            pubtypes = parse_pubmed_pubtypes(resp_xml.text)
                            rflag, rnote = classify_review_from_pubtypes(pubtypes)
                            if rflag:
                                is_review = True
                                review_source = "PubMed publication types (via DOI)"
                                review_notes = rnote + (f"; pubtypes={pubtypes[:6]}" if pubtypes else "")

            doi_explicit_ok = bool(doi_ok and doi_org_ok)
            doi_invalid = bool((not doi_ok) and (not doi_org_ok))

            if item:
                if year and crossref_year.isdigit():
                    y_ref, y_cr = int(year), int(crossref_year)
                    tol = 1 if y_ref <= 1990 else 2
                    year_ok = abs(y_cr - y_ref) <= tol

                author_ok = bool(first_author and any(surname_match(first_author, fam) for fam in meta_authors))
                journal_ok = journals_match(ref_journal, crossref_journal)
                title_sim = title_similarity(title, it_title)
                title_ok = bool(title_sim >= (0.78 if (year and year.isdigit() and int(year) <= 1990) else 0.75))

                cr_vol = str(item.get("volume") or "").strip()
                cr_issue = str(item.get("issue") or (item.get("journal-issue", {}) or {}).get("issue") or "").strip()
                cr_pages = str(item.get("page") or "").strip()
                vol_ok = bool(ref_vol and cr_vol and ref_vol == cr_vol)
                issue_ok = bool(ref_issue and cr_issue and ref_issue == cr_issue)
                pages_ok = pages_match(ref_pstart, ref_pend, cr_pages)

            if doi_source != "heuristic":
                doi_source = "explicit"

        elif pmid_match:
            pmid_val = pmid_match.group(1)
            resp = await fetch_pubmed(client, pmid_val)
            if resp.status_code == 200:
                summary = resp.json().get("result", {}).get(pmid_val, {})
                pubmed_id = pmid_val
                pubmed_journal = summary.get("fulljournalname", "")
                pubmed_year = summary.get("pubdate", "")
                m_year = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(pubmed_year))
                year_ok = bool(year and m_year and year == m_year.group(1))

                authors_list = [a.get("name", "") for a in summary.get("authors", [])]
                surname = (first_author.split()[0].lower() if first_author else "")
                author_ok = bool(
                    surname and any(
                        n.lower().split(",")[0].startswith(strip_accents(surname)[:5]) or
                        similar(strip_accents(surname), strip_accents(n.lower().split(",")[0])) >= 0.8
                        for n in authors_list
                    )
                )
                journal_ok = journals_match(ref_journal, pubmed_journal)
                if check_reviews:
                    resp_xml = await fetch_pubmed_efetch_xml(client, pmid_val)
                    if resp_xml.status_code == 200:
                        pubtypes = parse_pubmed_pubtypes(resp_xml.text)
                        rflag, rnote = classify_review_from_pubtypes(pubtypes)
                        is_review = bool(rflag)
                        review_source = "PubMed publication types"
                        review_notes = rnote + (f"; pubtypes={pubtypes[:6]}" if pubtypes else "")

        elif pmcid_val:
            pmid_from_pmc = ""
            resp = await fetch_pmc(client, pmcid_val)
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                uids = data.get("uids", [])
                uid_key = uids[0] if uids else None
                summary = data.get(uid_key or "", {})

                pubmed_journal = summary.get("fulljournalname") or summary.get("source") or ""
                pubmed_year = str(summary.get("pubdate") or summary.get("epubdate") or summary.get("sortpubdate") or "")
                m_year = re.search(r"\b(1[89]\d{2}|20\d{2})\b", pubmed_year)
                year_ok = bool(year and m_year and year == m_year.group(1))

                authors = summary.get("authors") or []
                candidate_names = []
                for a in authors:
                    if isinstance(a, dict):
                        if "name" in a and a["name"]:
                            candidate_names.append(a["name"])
                        elif "last" in a and a["last"]:
                            candidate_names.append(a["last"])

                surname = (first_author.split()[0].lower() if first_author else "")
                author_ok = bool(
                    surname and any(
                        (str(n).lower().split(",")[0]).startswith(strip_accents(surname)[:5]) or
                        similar(strip_accents(surname), strip_accents(str(n).lower().split(",")[0])) >= 0.8
                        for n in candidate_names
                    )
                )
                journal_ok = journals_match(ref_journal, pubmed_journal)
                pubmed_id = pmcid_val

                articleids = summary.get("articleids") or []
                for a in articleids:
                    if isinstance(a, dict) and str(a.get("idtype", "")).lower() == "pmid":
                        pmid_from_pmc = str(a.get("value", "")).strip()
                        break

            if check_reviews:
                if pmid_from_pmc:
                    resp_xml = await fetch_pubmed_efetch_xml(client, pmid_from_pmc)
                    if resp_xml.status_code == 200:
                        pubtypes = parse_pubmed_pubtypes(resp_xml.text)
                        rflag, rnote = classify_review_from_pubtypes(pubtypes)
                        is_review = bool(rflag)
                        review_source = "PubMed publication types (via PMCID)"
                        review_notes = rnote + (f"; pubtypes={pubtypes[:6]}" if pubtypes else "")
                else:
                    if not is_review and infer_review_from_title(title):
                        is_review = True
                        review_source = "Title heuristic (no PMID found)"
                        review_notes = "title pattern matched"

        else:
            resp = await fetch_crossref_bib(client, ref_clean)
            match_reason = "no query made"
            first_pass_item = None

            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
                if items:
                    had_crossref_candidate = True
                    first_pass_item, why = choose_best_crossref_item(ref_clean, items, first_author, year, ref_journal, title)
                    if first_pass_item:
                        item = first_pass_item
                        crossref_doi = item.get("DOI", "") or ""
                        crossref_journal = (item.get("container-title") or [""])[0]
                        iss = item.get("issued", {}).get("date-parts", [[None]])[0][0]
                        crossref_year = str(iss or "")
                        meta_authors = [a.get("family", "") for a in item.get("author", [])]
                        it_title = (item.get("title") or [""])[0]

                        if check_reviews and (not is_review):
                            if infer_review_from_title(it_title) or infer_review_from_title(title):
                                is_review = True
                                review_source = "Crossref/title heuristic"
                                review_notes = "title pattern matched"

                        doi_org_ok = False
                        if crossref_doi:
                            doi_org_ok = await check_doi_org_head(client, crossref_doi)

                        doi_ok = bool(crossref_doi)
                        doi_derived_ok = bool(crossref_doi and doi_org_ok)
                        doi_source = "derived" if crossref_doi and doi_source != "heuristic" else (doi_source or "")

                        if year and crossref_year.isdigit():
                            y_ref, y_cr = int(year), int(crossref_year)
                            year_ok = abs(y_cr - y_ref) <= (1 if y_ref <= 1990 else 2)
                        author_ok = bool(first_author and any(surname_match(first_author, fam) for fam in meta_authors))
                        journal_ok = journals_match(ref_journal, crossref_journal)
                        title_sim = title_similarity(title, it_title)
                        title_ok = bool(title_sim >= (0.78 if (year and year.isdigit() and int(year) <= 1990) else 0.75))

                        cr_vol = str(item.get("volume") or "").strip()
                        cr_issue = str(item.get("issue") or (item.get("journal-issue", {}) or {}).get("issue") or "").strip()
                        cr_pages = str(item.get("page") or "").strip()
                        vol_ok = bool(ref_vol and cr_vol and ref_vol == cr_vol)
                        issue_ok = bool(ref_issue and cr_issue and ref_issue == cr_issue)
                        pages_ok = pages_match(ref_pstart, ref_pend, cr_pages)

                        match_reason = f"accepted: {why}"
                    else:
                        match_reason = "no acceptable Crossref match (rejected loose hits)"
                else:
                    match_reason = "no Crossref items"
            else:
                match_reason = f"Crossref HTTP {resp.status_code}"

            if not first_pass_item:
                resp2 = await fetch_crossref_fielded(
                    client,
                    title=title,
                    author_surname=(first_author.split()[0] if first_author else ""),
                    container_title=ref_journal
                )
                if resp2.status_code == 200:
                    items2 = resp2.json().get("message", {}).get("items", [])
                    if items2:
                        had_crossref_candidate = True
                        item2, why2 = choose_best_crossref_item(ref_clean, items2, first_author, year, ref_journal, title)
                        if item2:
                            item = item2
                            crossref_doi = item.get("DOI", "") or ""
                            crossref_journal = (item.get("container-title") or [""])[0]
                            iss = item.get("issued", {}).get("date-parts", [[None]])[0][0]
                            crossref_year = str(iss or "")
                            meta_authors = [a.get("family", "") for a in item.get("author", [])]
                            it_title = (item.get("title") or [""])[0]

                            if check_reviews and (not is_review) and (infer_review_from_title(it_title) or infer_review_from_title(title)):
                                is_review = True
                                review_source = "Crossref/title heuristic"
                                review_notes = "title pattern matched"

                            doi_org_ok = False
                            if crossref_doi:
                                doi_org_ok = await check_doi_org_head(client, crossref_doi)

                            doi_ok = bool(crossref_doi)
                            doi_derived_ok = bool(crossref_doi and doi_org_ok)
                            doi_source = "derived" if crossref_doi and doi_source != "heuristic" else (doi_source or "")

                            if year and crossref_year.isdigit():
                                y_ref, y_cr = int(year), int(crossref_year)
                                year_ok = abs(y_cr - y_ref) <= (1 if y_ref <= 1990 else 2)
                            author_ok = bool(first_author and any(surname_match(first_author, fam) for fam in meta_authors))
                            journal_ok = journals_match(ref_journal, crossref_journal)
                            title_sim = title_similarity(title, it_title)
                            title_ok = bool(title_sim >= (0.78 if (year and year.isdigit() and int(year) <= 1990) else 0.75))

                            cr_vol = str(item.get("volume") or "").strip()
                            cr_issue = str(item.get("issue") or (item.get("journal-issue", {}) or {}).get("issue") or "").strip()
                            cr_pages = str(item.get("page") or "").strip()
                            vol_ok = bool(ref_vol and cr_vol and ref_vol == cr_vol)
                            issue_ok = bool(ref_issue and cr_issue and ref_issue == cr_issue)
                            pages_ok = pages_match(ref_pstart, ref_pend, cr_pages)

                            match_reason = f"accepted (fielded): {why2}"
                        else:
                            match_reason = "fielded search: no acceptable Crossref match"

    except Exception as e:
        base_result = "📄 Web Source" if primary_domain else "❌ Error"
        return {
            "Reference": ref,
            "Extracted DOI": doi_val or "",
            "DOI Source": doi_source or "",
            "Crossref DOI": crossref_doi,
            "Crossref Journal": crossref_journal,
            "Crossref Year": crossref_year,
            "PubMed ID": pubmed_id,
            "PubMed Journal": pubmed_journal,
            "PubMed Year": pubmed_year,
            "Score": 0,
            "Source URL": source_url,
            "doi_ok": doi_ok,
            "year_ok": year_ok,
            "author_ok": author_ok,
            "journal_ok": journal_ok,
            "Validation Result": base_result,
            "Raw Result": base_result,
            "Notes": f"lookup error: {e}",
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": doi_explicit_ok,
            "doi_derived_ok": doi_derived_ok,
        }

    result, reason, weighted_score = score_result(
        year_ok, author_ok, journal_ok,
        doi_explicit_ok=doi_explicit_ok,
        doi_derived_ok=doi_derived_ok,
        ref=ref_clean,
        shape_ok=has_bibliographic_shape(ref_clean),
        has_url=bool(primary_domain),
        had_crossref_candidate=had_crossref_candidate,
        title_ok=title_ok,
        vol_ok=vol_ok,
        issue_ok=issue_ok,
        pages_ok=pages_ok,
        doi_invalid=doi_invalid,
        manual_review_web=manual_review_web
    )

    if doi_invalid:
        reason = (reason + ("; " if reason else "") + "DOI present but invalid (no Crossref and no doi.org resolution)").strip("; ")

    if doi_val and not doi_explicit_ok and not doi_invalid:
        reason = (reason + ("; " if reason else "") + "DOI present but doi.org did not resolve/redirect").strip("; ")

    if doi_explicit_ok and result != "✅ Real":
        reason = (reason + ("; " if reason else "") +
                  "DOI resolves but metadata (author/title/journal/year/vol/issue/pages) mismatches").strip("; ")

    if (not doi_val and not pmid_match) and 'match_reason' in locals() and match_reason:
        reason = (reason + ("; " if reason else "") + match_reason)

    if (not doi_val and not pmid_match) and primary_domain and looks_scholarly_like(ref_clean):
        if result in {"⚠ Suspicious", "❌ possible falsification"}:
            reason = (reason + ("; " if reason else "") +
                      "journal-like reference with URL; attempted Crossref query but no acceptable match").strip("; ")

    if (not doi_val and not pmid_match and not primary_domain) and result in {"⚠ Suspicious", "❌ possible falsification"}:
        reason = (reason + ("; " if reason else "") + "no DOI/PMID/URL present; manual review recommended").strip("; ")

    if trusted_web and primary_domain and ("Web Source" in result or "Manual review" in result):
        reason = (reason + ("; " if reason else "") + "trusted domain").strip("; ")

    return {
        "Reference": ref,
        "Extracted DOI": (doi_val or ""),
        "DOI Source": doi_source or "",
        "Crossref DOI": (crossref_doi or "").rstrip(".,;:)"),
        "Crossref Journal": crossref_journal,
        "Crossref Year": crossref_year,
        "PubMed ID": pubmed_id,
        "PubMed Journal": pubmed_journal,
        "PubMed Year": pubmed_year,
        "Score": int(weighted_score),
        "Source URL": source_url,
        "doi_ok": doi_ok,
        "year_ok": year_ok,
        "author_ok": author_ok,
        "journal_ok": journal_ok,
        "Validation Result": result,
        "Raw Result": result,
        "Notes": reason,
        "Domain": primary_domain,
        "Domains": domains_joined,
        "doi_explicit_ok": doi_explicit_ok,
        "doi_derived_ok": doi_derived_ok,
        "Is Review": bool(is_review) if check_reviews else False,
        "Review Source": review_source if check_reviews else "",
        "Review Notes": review_notes if check_reviews else "",
    }

# =========================
# Excel Export (rich workbook)
# =========================

def build_excel_workbook(
    df: pd.DataFrame,
    debug_mode: bool = False,
    view: str = "Full",
    include_review: bool = False
) -> bytes:
    export_df = df.copy()

    if "Manual review" not in export_df.columns:
        export_df["Manual review"] = ""
    if "AL notes" not in export_df.columns:
        export_df["AL notes"] = ""

    if view == "Condensed":
        export_df["DOI score"] = export_df.apply(compute_doi_score, axis=1)

        main_cols = ["Ref #", "Validation Result", "Score", "DOI score"]
        if include_review:
            main_cols += ["Is Review"]

        main_cols += ["Manual review", "AL notes", "Notes", "Source URL", "Reference"]
        main = export_df[[c for c in main_cols if c in export_df.columns]].copy()

    else:
        base_order = ["Ref #", "Validation Result", "Score"]

        if include_review:
            base_order += ["Is Review", "Review Source", "Review Notes"]

        base_order += [
            "Manual review", "AL notes",
            "Reference", "Domain", "Domains", "Source URL",
            "Extracted DOI", "DOI Source", "Crossref DOI", "Crossref Journal", "Crossref Year",
            "PubMed ID", "PubMed Journal", "PubMed Year", "Notes"
        ]

        debug_order = ["doi_explicit_ok", "doi_derived_ok", "year_ok", "author_ok", "journal_ok"]
        desired = base_order + (debug_order if debug_mode else [])
        cols = [c for c in desired if c in export_df.columns]
        main = export_df[cols].copy()

    for c in main.columns:
        if main[c].dtype == bool:
            main[c] = main[c].map({True: "True", False: "False"})
        main[c] = main[c].astype(str)

    counts = export_df["Validation Result"].value_counts().rename_axis("Category").reset_index(name="Count")

    top_domains = (
        export_df["Domain"].replace("", pd.NA).dropna().value_counts()
        .rename_axis("Domain").reset_index(name="Count").head(50)
    )

    if "Domain" in export_df.columns and "Validation Result" in export_df.columns:
        dom_mat = (
            export_df.assign(Domain=export_df["Domain"].replace("", pd.NA))
            .dropna(subset=["Domain"])
            .pivot_table(
                index="Domain",
                columns="Validation Result",
                values="Ref #",
                aggfunc="count",
                fill_value=0
            )
            .reset_index()
        )
    else:
        dom_mat = pd.DataFrame()

        issues = export_df[
        export_df["Validation Result"].isin([
            "⚠ Suspicious",
            "❌ possible falsification",
            "❌ Error",
            MANUAL_REVIEW_LABEL
        ])
    ].copy()

    if view == "Condensed":
        issues["DOI score"] = issues.apply(compute_doi_score, axis=1)

        issue_cols = ["Ref #", "Validation Result", "Score", "DOI score"]
        if include_review:
            issue_cols += ["Is Review"]

        issue_cols += ["Manual review", "AL notes", "Notes", "Source URL", "Reference"]
        issues = issues[[c for c in issue_cols if c in issues.columns]].copy()
    else:
        issues = issues[[c for c in main.columns if c in issues.columns]].copy()

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        writer.book.strings_to_urls = False
        workbook = writer.book

        fmt_header = workbook.add_format({"bold": True, "text_wrap": True, "valign": "top"})
        fmt_review_header = workbook.add_format({
            "bold": True,
            "text_wrap": True,
            "valign": "top",
            "bg_color": "#d9eaf7",
            "font_color": "#1f4e78"
        })
        fmt_wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        fmt_real = workbook.add_format({"bg_color": "#d4edda", "font_color": "#155724"})
        fmt_web = workbook.add_format({"bg_color": "#e2e3e5", "font_color": "#383d41"})
        fmt_manual = workbook.add_format({"bg_color": "#fff4cc", "font_color": "#7a5c00"})
        fmt_susp = workbook.add_format({"bg_color": "#fff3cd", "font_color": "#856404"})
        fmt_ai = workbook.add_format({"bg_color": "#f8d7da", "font_color": "#721c24"})
        fmt_err = workbook.add_format({"bg_color": "#fde2e1", "font_color": "#7a1b17"})

        def autosize(ws, dataframe, wrap_cols=None):
            wrap_cols = wrap_cols or []
            for j, col in enumerate(dataframe.columns):
                series = dataframe[col].astype(str)
                max_len = max([len(col)] + [len(s) for s in series.tolist()]) if len(series) else len(col)
                if col in ("Reference", "Notes", "AL notes", "Review Notes"):
                    cap = 90
                elif col in ("Domains", "Journal", "PubMed Journal", "Crossref Journal", "Review Source"):
                    cap = 38
                else:
                    cap = 28
                width = min(max_len + 2, cap)
                ws.set_column(j, j, width, fmt_wrap if col in wrap_cols else None)

        def add_manual_review_dropdown(ws, dataframe):
            if "Manual review" in dataframe.columns:
                col_idx = list(dataframe.columns).index("Manual review")
                nrows = max(len(dataframe), 200)
                ws.data_validation(
                    1, col_idx, nrows, col_idx,
                    {"validate": "list", "source": ["Yes", "No"]}
                )

        def style_headers(ws, dataframe):
            for j, col in enumerate(dataframe.columns):
                if col in ("Is Review", "Review Source", "Review Notes"):
                    ws.write(0, j, col, fmt_review_header)
                else:
                    ws.write(0, j, col, fmt_header)

        def write_table(sheet_name, dataframe, wrap_cols=None):
            if dataframe.empty:
                pd.DataFrame({"Info": ["No data"]}).to_excel(writer, sheet_name=sheet_name, index=False)
                return

            dataframe.to_excel(writer, sheet_name=sheet_name, index=False, startrow=0)
            ws = writer.sheets[sheet_name]
            nrows, ncols = dataframe.shape

            ws.set_row(0, None, fmt_header)
            style_headers(ws, dataframe)
            ws.freeze_panes(1, 1)
            autosize(ws, dataframe, wrap_cols=wrap_cols)
            add_manual_review_dropdown(ws, dataframe)
            ws.autofilter(0, 0, nrows, ncols - 1)

            if "Validation Result" in dataframe.columns:
                col_idx = list(dataframe.columns).index("Validation Result")
                first = 1
                last = nrows
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "Real", "format": fmt_real
                })
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "Manual review", "format": fmt_manual
                })
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "Web Source", "format": fmt_web
                })
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "Suspicious", "format": fmt_susp
                })
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "AI", "format": fmt_ai
                })
                ws.conditional_format(first, col_idx, last, col_idx, {
                    "type": "text", "criteria": "containing", "value": "Error", "format": fmt_err
                })

        counts.to_excel(writer, sheet_name="Summary", index=False, startrow=0)
        ws_sum = writer.sheets["Summary"]
        ws_sum.set_row(0, None, fmt_header)
        ws_sum.freeze_panes(1, 0)
        ws_sum.autofilter(0, 0, len(counts), len(counts.columns) - 1)

        startrow = len(counts) + 3
        ws_sum.write_string(startrow, 0, "Top Domains (first 50):", fmt_header)
        if not top_domains.empty:
            top_domains.to_excel(writer, sheet_name="Summary", index=False, startrow=startrow + 1)
            ws_sum.autofilter(startrow + 1, 0, startrow + 1 + len(top_domains), len(top_domains.columns) - 1)
        else:
            ws_sum.write_string(startrow + 1, 0, "No domains found.")

        wrap_cols = [
            "Reference", "Notes", "AL notes", "Review Notes",
            "Domains", "Journal", "PubMed Journal", "Crossref Journal", "Review Source"
        ]
        write_table("Results", main, wrap_cols=wrap_cols)
        write_table("Issues", issues, wrap_cols=wrap_cols)

        if not dom_mat.empty:
            dom_mat.to_excel(writer, sheet_name="Domains", index=False)
            ws_dom = writer.sheets["Domains"]
            ws_dom.set_row(0, None, fmt_header)
            ws_dom.freeze_panes(1, 1)
            ws_dom.autofilter(0, 0, len(dom_mat), len(dom_mat.columns) - 1)

    return out.getvalue()

# =========================
# Sanity check (format)
# =========================

def sanity_check(raw_text: str) -> list[str]:
    warnings = []
    lines = [ln for ln in re.sub(r"\r\n?", "\n", raw_text or "").split("\n") if ln.strip()]
    if not lines:
        return warnings
    year_hits = len(re.findall(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", raw_text or ""))
    if year_hits < max(1, int(0.3 * len(lines))):
        warnings.append("Few lines contain a valid year — may not be in a standard reference style.")
    if re.search(r"\[\d+\]", raw_text or "") and year_hits == 0:
        warnings.append("Detected inline numeric citations (e.g., [1], [2]) rather than a reference list.")
    if len(raw_text or "") > 0 and (raw_text.count("http") > year_hits * 2):
        warnings.append("Many raw URLs without bibliographic metadata — splitting/validation may be unreliable.")
    return warnings

# =========================
# Streamlit UI
# =========================

st.title("📚 Agent Ref")
st.subheader("BETA VERSION")
st.caption("This is a test version for new functions")

if "sid" not in st.session_state:
    st.session_state["sid"] = str(uuid.uuid4())

st.write(
    """
    Validates references via DOI / PubMed / Crossref and classifies results:
    - ✅ Real → DOI verified with resolver + bibliographic match; OR Crossref-derived DOI verified at doi.org + match; OR PMCID/PMID verification
    - 🟡 Manual review – web source / report / dataset → organisational or dataset/report-style web source with URL
    - 📄 Web Source (trusted) / 📄 Web Source → general website/URL
    - ⚠ Suspicious → partial mismatch or unverifiable but well-formed
    - ❌ possible falsification → fabricated pairing or failed checks
    """
)

with st.expander("How the result is decided"):
    st.markdown("""
A reference is **only** marked **✅ Real** when it passes a verification rule.  
The score helps show how strong the match is, but the score alone does **not** make a reference real.

**✅ Real**
- A DOI works and the key details match well enough, or
- If there is no DOI, the **year**, **author**, and **journal** all match trusted records

**🟡 Manual review – web source / report / dataset**
- The reference looks like an organisational source, fact sheet, report, repository, or dataset
- It contains a URL, but it is not a standard journal-style reference
- These are not marked false automatically and should be checked manually

**⚠ Suspicious**
- Some parts match, but the reference cannot be fully confirmed, or
- It looks like a genuine reference, but there is not enough evidence to verify it properly

**❌ Possible falsification**
- A DOI is present but does not work and the rest of the reference is weak, or
- The DOI works but **both** the author and title do not match, or
- The reference fails most checks and does not look reliable
""")

with st.expander("How the score is calculated"):
    st.markdown("""
The score is a simple guide that shows how many parts of the reference match trusted records.

**Points**
- **4 points** for a DOI given in the reference that works
- **2 points** for a DOI found from other checks that works
- **2 points** if the author matches
- **2 points** if the title matches
- **2 points** if the year matches
- **1 point** if the journal matches
- **1 point** if the volume matches
- **1 point** if the issue matches
- **1 point** if the pages match
- **1 point** if the reference is in a believable format

**Maximum score: 17**

**How to read the score**
- **0 to 3**: very weak match
- **4 to 7**: limited match, usually needs caution
- **8 to 11**: moderate match, may look plausible but still not confirmed
- **12 to 17**: strong match

**Important**
A higher score means the reference looks stronger, but the score does **not** decide the final label on its own.

A reference is only marked **✅ Real** if it passes the verification rules.  
This means a high score can still be **⚠ Suspicious** or **❌ Possible falsification** if key checks fail.
""")

st.markdown(
    """
    <style>
    .wrap-text { white-space: normal !important; word-wrap: break-word !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

debug_mode = st.checkbox("Show debug details", value=False)

check_reviews = st.checkbox(
    "Detect reviews (slower)",
    value=False,
    help="If enabled, Agent Ref will try to detect reviews using PubMed publication types and title heuristics."
)

table_view = st.selectbox(
    "Table view",
    ["Full", "Condensed"],
    index=0,
    help="Condensed view shows only key columns for quick marking."
)

if debug_mode:
    st.info("Debug shows extra check columns and flags for explicit/derived/heuristic DOI, plus which checks passed.")

def clear_ref_input():
    st.session_state["ref_input"] = ""

if "ref_input" not in st.session_state:
    st.session_state["ref_input"] = ""

input_col, clear_col = st.columns([8, 1])

with input_col:
    user_input = st.text_area(
        "Paste references here:",
        height=300,
        key="ref_input"
    )

with clear_col:
    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    st.button("Clear box", on_click=clear_ref_input, use_container_width=True)

if "prefix" not in st.session_state:
    st.session_state["prefix"] = ""
prefix_input = st.text_input(
    "Optional file name prefix",
    value=st.session_state["prefix"],
    placeholder="e.g., cohortA_run3",
    help="If provided, this will be prepended to the CSV and Excel filenames."
)
st.session_state["prefix"] = prefix_input
prefix = sanitize_prefix(st.session_state["prefix"])

if user_input.strip():
    warns = sanity_check(user_input)
    if warns:
        st.warning("⚠️ Reference formatting may be inconsistent:\n\n- " + "\n- ".join(warns))

def _utc_now():
    return datetime.now(timezone.utc)

def _log_run(*, input_text: str, split_refs: list[str], result_counts: dict, error_text: str | None):
    try:
        if not st.secrets.get("LOGGING_ENABLED", False):
            return
        db_url = st.secrets.get("DB_URL", "")
        if not db_url:
            return
    except FileNotFoundError:
        return
    except Exception:
        return

    # conn = psycopg2.connect(db_url)
    # try:
    #     with conn, conn.cursor() as cur:
    #         cur.execute(
    #             """
    #             insert into agent_ref_runs
    #             (ts_utc, session_id, input_text, split_refs, result_counts, error_text)
    #             values (%s, %s, %s, %s, %s, %s)
    #             """,
    #             (
    #                 _utc_now(),
    #                 st.session_state["sid"],
    #                 input_text,
    #                 Json(split_refs),
    #                 Json(result_counts),
    #                 error_text,
    #             ),
    #         )
    # finally:
    #     conn.close()
    return

st.caption(
    "Clicking ‘Check References’ logs the reference list, validation summary, and errors for improvement purposes. "
    "No personal user data is collected."
)

# =========================
# Display helpers
# =========================

def compute_doi_score(row: pd.Series) -> int:
    """
    Simple numeric DOI score for the condensed table:
      2 = explicit DOI hard-resolved (Crossref + doi.org)
      1 = derived DOI resolved at doi.org
      0 = no usable DOI
     -1 = DOI present but invalid (no Crossref and no doi.org)
    """
    extracted = str(row.get("Extracted DOI", "") or "").strip()
    derived = str(row.get("Crossref DOI", "") or "").strip()

    doi_present = bool(extracted or derived)

    if bool(row.get("doi_explicit_ok", False)):
        return 2
    if bool(row.get("doi_derived_ok", False)):
        return 1

    notes = str(row.get("Notes", "") or "").lower()
    if doi_present and "invalid doi" in notes:
        return -1

    return 0

def add_link_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["DOI link"] = out.apply(
        lambda r: (
            "https://doi.org/" + (str(r.get("Extracted DOI") or r.get("Crossref DOI") or "").strip().rstrip(".,;:)"))
        ) if (r.get("Extracted DOI") or r.get("Crossref DOI")) else "",
        axis=1
    )

    def _pubmed_link(val: str) -> str:
        v = (val or "").strip()
        if not v:
            return ""
        if v.upper().startswith("PMC"):
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{v}/"
        if v.isdigit():
            return f"https://pubmed.ncbi.nlm.nih.gov/{v}/"
        return ""

    out["PubMed link"] = out.apply(lambda r: _pubmed_link(str(r.get("PubMed ID") or "")), axis=1)
    out["Source link"] = out.apply(lambda r: str(r.get("Source URL") or "").strip(), axis=1)

    return out

    def _pubmed_link(val: str) -> str:
        v = (val or "").strip()
        if not v:
            return ""
        if v.upper().startswith("PMC"):
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{v}/"
        if v.isdigit():
            return f"https://pubmed.ncbi.nlm.nih.gov/{v}/"
        return ""

    out["PubMed link"] = out.apply(lambda r: _pubmed_link(str(r.get("PubMed ID") or "")), axis=1)

    return out

def build_display_df(df: pd.DataFrame, *, view: str, include_review: bool) -> pd.DataFrame:
    df2 = df.copy()
    df2["DOI score"] = df2.apply(compute_doi_score, axis=1)
    df2 = add_link_columns(df2)

    if view == "Condensed":
        cols = ["Ref #", "Validation Result", "Score", "DOI score", "Notes", "Source link", "DOI link"]
        if include_review:
            cols.insert(3, "Is Review")
        for c in cols:
            if c not in df2.columns:
                df2[c] = ""
        return df2[cols].copy()

    cols = [
        "Ref #",
        "Validation Result",
        "Score",
        "DOI score",
        "Is Review", "Review Source",
        "Reference",
        "Domain", "Domains",
        "Source URL",
        "Extracted DOI", "DOI Source",
        "Crossref DOI", "Crossref Journal", "Crossref Year",
        "PubMed ID", "PubMed Journal", "PubMed Year",
        "Notes",
        "Source link",
        "DOI link",
        "PubMed link",
    ]

    if not include_review:
        cols = [c for c in cols if c not in ("Is Review", "Review Source")]

    debug_cols = ["doi_explicit_ok", "doi_derived_ok", "year_ok", "author_ok", "journal_ok"]
    if debug_mode:
        cols += debug_cols

    for c in cols:
        if c not in df2.columns:
            df2[c] = ""

    existing_cols = [c for c in cols if c in df2.columns]
    return df2[existing_cols].copy()

if st.button("Check References"):
    if user_input.strip():
        refs: list[str] = []
        result_counts: dict = {}
        error_text: str | None = None

        try:
            refs = split_references(user_input)

            if debug_mode:
                st.write(f"🔎 Detected {len(refs)} references")
                for i, r in enumerate(refs[:8], 1):
                    st.write(f"{i}. {r[:250]}{'…' if len(r)>250 else ''}")

            async def process_all(_refs):
                results = []
                total = len(_refs)
                progress = st.progress(0)
                async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
                    for i, ref in enumerate(_refs, start=1):
                        result = await validate_single_ref(client, ref, debug_mode, check_reviews=check_reviews)
                        result["Ref #"] = i
                        results.append(result)
                        progress.progress(i / total)
                return results

            results = asyncio.run(process_all(refs))
            df = pd.DataFrame(results)
            counts = df["Validation Result"].value_counts()
            result_counts = counts.to_dict()

            st.subheader("📊 Summary")
            st.write({
                "Filename prefix": prefix,
                "Total references": int(len(df)),
                "✅ Real": int(counts.get("✅ Real", 0)),
                "🟡 Manual review": int(counts.get(MANUAL_REVIEW_LABEL, 0)),
                "📄 Web Source (trusted)": int(counts.get("📄 Web Source (trusted)", 0)),
                "📄 Web Source": int(counts.get("📄 Web Source", 0)),
                "⚠ Suspicious": int(counts.get("⚠ Suspicious", 0)),
                "❌ possible falsification": int(counts.get("❌ possible falsification", 0)),
                "❌ Error": int(counts.get("❌ Error", 0)),
            })

            used_fallback = False
            df_display = build_display_df(df, view=table_view, include_review=check_reviews)

            try:
                from streamlit import column_config
                cfg = {}
                if "Source link" in df_display.columns:
                    cfg["Source link"] = column_config.LinkColumn("Source link")
                if "DOI link" in df_display.columns:
                    cfg["DOI link"] = column_config.LinkColumn("DOI link")
                if "PubMed link" in df_display.columns:
                    cfg["PubMed link"] = column_config.LinkColumn("PubMed link")

                st.data_editor(
                    df_display,
                    column_config=cfg,
                    use_container_width=True,
                    height=560,
                    hide_index=True
                )

            except Exception:
                used_fallback = True

                def highlight_row(val):
                    if "Real" in val:
                        return "background-color: #d4edda; color: #155724"
                    if "Manual review" in val:
                        return "background-color: #fff4cc; color: #7a5c00"
                    if "Web Source" in val:
                        return "background-color: #e2e3e5; color: #383d41"
                    if "Suspicious" in val:
                        return "background-color: #fff3cd; color: #856404"
                    if "AI" in val:
                        return "background-color: #f8d7da; color: #721c24"
                    if "Error" in val:
                        return "background-color: #fde2e1; color: #7a1b17"
                    return ""

                st.dataframe(
                    df_display.style.map(highlight_row, subset=["Validation Result"]),
                    use_container_width=True,
                    height=560
                )
                st.caption("Note: Link columns shown as plain text in this Streamlit version.")

            today = date.today().isoformat()

            full_xlsx_filename = f"{prefix}validated_references_full_{today}.xlsx"
            condensed_xlsx_filename = f"{prefix}validated_references_condensed_{today}.xlsx"

            full_xlsx_bytes = build_excel_workbook(
                df,
                debug_mode=debug_mode,
                view="Full",
                include_review=check_reviews
            )
            st.download_button(
                "Download full Excel workbook",
                full_xlsx_bytes,
                full_xlsx_filename,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_full_xlsx"
            )

            condensed_xlsx_bytes = build_excel_workbook(
                df,
                debug_mode=False,
                view="Condensed",
                include_review=check_reviews
            )
            st.download_button(
                "Download condensed Excel workbook",
                condensed_xlsx_bytes,
                condensed_xlsx_filename,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_condensed_xlsx"
            )

            if used_fallback:
                st.markdown("**Links:**")
                link_rows = []
                for _, r in df_display.iterrows():
                    if r.get("Source link") or r.get("DOI link") or r.get("PubMed link"):
                        parts = []
                        if r.get("Source link"):
                            parts.append(f"[Source]({r['Source link']})")
                        if r.get("DOI link"):
                            parts.append(f"[DOI]({r['DOI link']})")
                        if r.get("PubMed link"):
                            parts.append(f"[PubMed/PMC]({r['PubMed link']})")

                        link_rows.append(f"- Ref {int(r['Ref #'])}: " + " | ".join(parts))
                if link_rows:
                    st.markdown("\n".join(link_rows))

        except Exception as e:
            error_text = str(e)
            raise

        finally:
            _log_run(
                input_text=user_input,
                split_refs=refs,
                result_counts=result_counts,
                error_text=error_text
            )

    else:
        st.warning("Please paste some references first.")

FOOTER = """
<style>
.app-footer {
  position: fixed;
  right: 12px;
  bottom: 10px;
  padding: 6px 10px;
  font-size: 0.8rem;
  opacity: 0.65;
  z-index: 9999;
  background: transparent;
}
.app-footer a { text-decoration: none; }
</style>

<div class="app-footer">
  Created by <b>Mark Hintze</b> · <a href="mailto:FoxHin5431@users.noreply.github.com">FoxHin5431@users.noreply.github.com</a>
</div>
"""
st.markdown(FOOTER, unsafe_allow_html=True)
