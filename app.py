# reference_checker_app.py
# Streamlit app: bulk reference validator (DOI / PubMed / Crossref) with robust splitting & Excel export
# Updates applied (hardened):
# - Explicit DOI scoring separated from derived-DOI metadata discovery
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
# - Derived DOI is metadata-only and never supplies DOI score/acceptance evidence
# - Strong title/author matches with multiple confirmed metadata conflicts are
#   classified before the numeric score fallback
# - NEW: leading bullets / list markers stripped before parsing
# - NEW: organisational/report/dataset web references with URLs routed to manual review rather than false

import streamlit as st
import pandas as pd
import re
import html
import urllib.parse
import asyncio
import httpx
import io
from datetime import date
from difflib import SequenceMatcher
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
#import psycopg2
#from psycopg2.extras import Json

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

AI_URL_NOTE = "AI tracking parameter detected: utm_source=chatgpt"

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
    """
    Fix only obvious URL line/spacing breaks without swallowing citation text
    such as (Accessed 24 April 2026).
    """
    if not text:
        return text

    fixed_lines = []
    for line in re.split(r"\r?\n", text):
        # Fix spaces immediately after a hyphen inside a URL on the same line.
        # Example: https://site.org/abc- def -> https://site.org/abc-def
        line = re.sub(r"(https?://\S*?)-[ \t]+(\S+)", r"\1-\2", line)

        # Fix spaces inside explicit DOI URLs only, not after the URL.
        # Example: https://doi.org/10.1000/abc 123 -> https://doi.org/10.1000/abc123
        def _fix_doi_url(match):
            url = match.group(0)
            return re.sub(r"[ \t]+", "", url)

        line = re.sub(
            r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/[^\s<>\]]+",
            _fix_doi_url,
            line,
            flags=re.I
        )

        fixed_lines.append(line)

    return "\n".join(fixed_lines)

def clean_ref(ref: str) -> str:
    s = ref or ""
    s = strip_leading_list_marker(s)
    s = normalise_broken_url_spacing(s)
    s = s.replace("\u00a0", " ")

    s = re.sub(r"\[(?:Online|online)\]", "", s, flags=re.I)
    s = re.sub(r"\bAvailable at\b.*?$", "", s, flags=re.I)
    s = re.sub(r"\b(?:Accessed|Assessed)(?: date)?\b.*?$", "", s, flags=re.I)
    s = " ".join(s.split())
    return s.strip(" .")


def extract_first_author(ref: str) -> str:
    authors = extract_author_surnames(ref)
    return authors[0] if authors else ""

YEAR_TOKEN_RE = re.compile(
    r"\b(?P<year>1[89]\d{2}|20\d{2})(?P<suffix>[a-z])?\b",
    flags=re.I,
)

ET_AL_RE = re.compile(r"\bet\s+al\.?\b", flags=re.I)


def reference_uses_et_al(ref: str) -> bool:
    return bool(ET_AL_RE.search(ref or ""))


def extract_author_surnames(ref: str) -> list[str]:
    """Extract every explicitly supplied author surname before the title/year.

    The parser covers surname-first Harvard forms, initial-based Vancouver
    forms, a final given-name-first author, and organisational authors. It does
    not invent the omitted names represented by ``et al.``.
    """
    text = strip_leading_list_marker(ref or "").lstrip(" \"'“”‘’")
    if not text:
        return []

    boundaries = []
    year_match = YEAR_TOKEN_RE.search(text)
    if year_match:
        boundaries.append(year_match.start())
    quote_positions = [
        position
        for marker in ("‘", "'", "“", '"')
        if (position := text.find(marker, 1)) >= 0
    ]
    if quote_positions:
        boundaries.append(min(quote_positions))

    et_al_match = ET_AL_RE.search(text)
    if et_al_match:
        boundaries.append(et_al_match.end())

    prefix = text[: min(boundaries)] if boundaries else text[:350]
    prefix = prefix.strip(" ,.;:()[]")
    if not prefix:
        return []

    leading_organisation = text.split(".", 1)[0].strip(" ,.;:()[]")
    if not year_match and len(leading_organisation.split()) >= 2 and re.search(
        r"\b(?:university|organisation|organization|service|institute|agency|"
        r"department|ministry|council|centers?|nhs|unicef|who)\b",
        leading_organisation,
        flags=re.I,
    ):
        return [leading_organisation]

    name_word = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’`\-]+"
    family_name = (
        rf"(?:(?:[Dd]e|[Dd]el|[Dd]er|[Dd]i|[Dd]a|[Dd]os|[Dd]u|"
        rf"[Vv]an|[Vv]on|[Ll]a|[Ll]e)\s+){{0,3}}{name_word}"
    )
    surnames: list[str] = []

    # Surname-first forms: ``Paisley, M.F.`` or ``Stern, Claudio D``.
    for match in re.finditer(
        rf"(?:^|,\s*|\band\s+|&\s+)({family_name})"
        rf"\s*,\s*(?=[A-ZÀ-ÖØ-Þ])",
        prefix,
        flags=re.UNICODE,
    ):
        surnames.append(match.group(1).strip())

    # Vancouver forms: ``Bekaii-Saab TS, Yaeger R, Spira AI``.
    leading_full_given_names = bool(
        re.match(rf"^{name_word},\s*{name_word}(?:\s+[A-Z]\.?)?\s*,", prefix)
    )
    first_comma_position = prefix.find(",")
    for match in re.finditer(
        rf"(?:^|,\s*|\band\s+|&\s+)({name_word})\s+"
        r"(?:[A-Z](?:[A-Z.\-]{0,8}))(?=\s*(?:,|\band\b|&|\bet\s+al\b|\.))",
        prefix,
        flags=re.UNICODE,
    ):
        if leading_full_given_names and match.start() == first_comma_position:
            continue
        surnames.append(match.group(1).strip())

    # A final given-name-first author: ``and Agnieszka M Piatkowska``.
    final_given_first = re.search(
        rf"(?:\band\s+|&\s+)(?:{name_word}\s+)+(?:[A-Z]\.?\s+)*({name_word})\s*\.?$",
        prefix,
        flags=re.UNICODE,
    )
    if final_given_first:
        surnames.append(final_given_first.group(1).strip())

    supplied = [
        surname
        for surname in surnames
        if strip_accents(surname).casefold() not in {"", "et", "al"}
    ]

    # Repeated surnames can represent different co-authors (for example,
    # ``Kim, J., Kim, H. and Bang, D.``), so order and duplicates are retained.
    if supplied:
        return supplied

    # Organisational author before an early year, e.g. ``The Open University``.
    if year_match and year_match.start() <= 160:
        organisation = text[: year_match.start()].strip(" ,.;:()[]")
        if organisation and not re.search(r"\b(?:vol|volume|journal|pp?)\.?\b", organisation, re.I):
            return [organisation]

    organisation = leading_organisation
    if len(organisation.split()) >= 2 and re.search(
        r"\b(?:university|organisation|organization|service|institute|agency|"
        r"department|ministry|council|centers?|nhs|unicef|who)\b",
        organisation,
        flags=re.I,
    ):
        return [organisation]

    # Conservative compatibility fallback for an unrecognised personal style.
    fallback = re.match(rf"^({name_word})", text, flags=re.UNICODE)
    return [fallback.group(1)] if fallback else []


def extract_year(ref: str) -> str:
    m = YEAR_TOKEN_RE.search(ref or "")
    return m.group(0) if m else ""


def parse_year_token(value: str) -> tuple[int | None, str]:
    """Return the numeric year and optional citation suffix from a year token."""
    m = YEAR_TOKEN_RE.search(value or "")
    if not m:
        return None, ""
    return int(m.group("year")), (m.group("suffix") or "").lower()

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

    # Accept straight/curly single or double quotation marks. The look-ahead
    # helps distinguish a closing quote from an apostrophe inside the title.
    quote_patterns = [
        r"['‘]\s*(.{2,300}?)\s*['’](?=\s*(?:[,.;]|[A-Z]))",
        r'["“]\s*(.{2,300}?)\s*["”](?=\s*(?:[,.;]|[A-Z]))',
    ]
    for pattern in quote_patterns:
        m_q = re.search(pattern, ref)
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

def clean_doi_value(raw: str) -> str:
    """
    Cleans DOI strings and removes common trailing citation/access-date text.
    """
    if not raw:
        return ""

    s = urllib.parse.unquote(str(raw)).strip()

    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)

    # URL query strings and fragments are transport metadata, not part of the
    # DOI. This is especially important for links carrying AI/marketing UTM
    # parameters, which would otherwise be sent to Crossref as part of the DOI.
    s = re.split(r"[?#]", s, maxsplit=1)[0]

    # Stop before access-date text, including cases where spacing has already collapsed:
    # 10.7326/M14-0737(Assessed24April2026)
    s = re.split(r"(?i)(?:\s|\(|%28)*(?:accessed|assessed)\b", s)[0]

    # Remove common trailing punctuation.
    # A trailing backslash commonly appears when references have passed
    # through Markdown/CSV escaping. It is transport punctuation, not DOI
    # content, and must not be sent to Crossref or doi.org.
    s = s.rstrip(".,;:]}>'\"\\")

    # Remove a trailing unmatched closing bracket.
    while s.endswith(")") and s.count("(") < s.count(")"):
        s = s[:-1].rstrip(".,;:\\")

    return s

def extract_doi(ref: str):
    if not ref:
        return None

    patterns = [
        r"\[(10\.\d{4,9}/[^\s<>\]]+)\]\(https?://(?:dx\.)?doi\.org/\1\)",
        r"\[(10\.\d{4,9}/[^\s<>\]]+)\]",
        r"doi\.org/(10\.\d{4,9}/[^\s<>\]]+)",
        r"\bdoi:\s*(10\.\d{4,9}/[^\s<>\]]+)",
        r"\b(10\.\d{4,9}/[^\s<>\]]+)",
    ]

    for pat in patterns:
        m = re.search(pat, ref, flags=re.I)
        if m:
            doi = clean_doi_value(m.group(1))
            return doi if doi else None

    return None
    
def clean_url_value(url: str) -> str:
    if not url:
        return ""

    u = str(url).strip()

    # Stop before access-date text.
    u = re.split(r"(?i)(?:\s|\(|%28)*(?:accessed|assessed)\b", u)[0]

    # Remove common trailing punctuation.
    u = u.rstrip(".,;")

    # Remove unmatched trailing closing bracket.
    while u.endswith(")") and u.count("(") < u.count(")"):
        u = u[:-1].rstrip(".,;")

    return u

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
        u = clean_url_value(u)
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    return out

def extract_primary_url(ref: str) -> str:
    urls = extract_urls(ref)
    return urls[0] if urls else ""

def has_chatgpt_utm_source(ref: str) -> bool:
    """
    Flag URLs whose utm_source identifies ChatGPT.

    Handles case differences, HTML-escaped query separators, and one or more
    layers of percent encoding while avoiding unrelated UTM sources.
    """
    if not ref:
        return False

    def is_chatgpt_value(value: object) -> bool:
        normalised = urllib.parse.unquote_plus(str(value or ""))
        normalised = html.unescape(normalised).strip().casefold().rstrip("/")
        return normalised in {"chatgpt", "chatgpt.com", "www.chatgpt.com"}

    candidates = []
    candidate = normalise_broken_url_spacing(str(ref))
    for _ in range(3):
        candidate = html.unescape(candidate)
        if candidate not in candidates:
            candidates.append(candidate)
        decoded = urllib.parse.unquote_plus(candidate)
        if decoded == candidate:
            break
        candidate = decoded

    for candidate in candidates:
        for match in re.finditer(
            r"(?i)(?:[?&])utm_source\s*=\s*([^&#\s)\]]+)",
            candidate,
        ):
            if is_chatgpt_value(match.group(1).rstrip(".,;")):
                return True

        for url in extract_urls(candidate):
            try:
                parsed = urllib.parse.urlparse(url)
                for key, value in urllib.parse.parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                ):
                    if key.strip().casefold() == "utm_source" and is_chatgpt_value(value):
                        return True
            except (TypeError, ValueError):
                continue

    return False
    
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
    pages = re.search(r"\bpp?\.\s*(?:\d+|[A-Z]{1,5}\d{3,})\b", ref, flags=re.I)
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

def normalize_author_name(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = strip_accents(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def surname_match(submitted_surname: str, metadata_surname: str) -> bool:
    if not submitted_surname or not metadata_surname:
        return False
    submitted = normalize_author_name(submitted_surname)
    metadata = normalize_author_name(metadata_surname)
    if not submitted or not metadata:
        return False
    if submitted == metadata:
        return True
    if min(len(submitted), len(metadata)) >= 5 and (
        submitted.startswith(metadata) or metadata.startswith(submitted)
    ):
        return True
    return similar(submitted, metadata) >= 0.84


def metadata_author_surnames(authors: object) -> list[str]:
    """Return comparable surnames from Crossref or PubMed author objects."""
    out = []
    for author in authors or []:
        value = ""
        if isinstance(author, dict):
            value = str(
                author.get("family")
                or author.get("last")
                or author.get("name")
                or ""
            ).strip()
        else:
            value = str(author or "").strip()

        if not value:
            continue
        if re.search(
            r"\b(?:on behalf of|consortium|collaborative group|study group|staff editors?)\b",
            value,
            flags=re.I,
        ):
            # Collective credits are useful metadata but are not an omitted
            # personal co-author in the student's author list.
            continue
        if "," in value:
            value = value.split(",", 1)[0].strip()
        elif re.search(r"\s+[A-Z][A-Z.\-]*$", value):
            # PubMed normally returns ``Surname AB``.
            value = value.rsplit(" ", 1)[0].strip()
        out.append(value)
    return out


def compare_author_lists(
    submitted_authors: list[str],
    metadata_authors: list[str],
    *,
    uses_et_al: bool = False,
) -> dict:
    """Compare all explicitly supplied authors with trusted metadata.

    Complete lists need at least 80% coverage in both directions. With
    ``et al.``, each explicitly named author must match the corresponding
    leading metadata author, while omitted names are deliberately ignored.
    """
    submitted = [a for a in submitted_authors if normalize_author_name(a)]
    metadata = [a for a in metadata_authors if normalize_author_name(a)]
    result = {
        "ok": False,
        "method": "not checked",
        "matched_count": 0,
        "submitted_count": len(submitted),
        "metadata_count": len(metadata),
        "submitted_coverage": 0.0,
        "metadata_coverage": 0.0,
        "first_author_ok": False,
    }
    if not submitted:
        result["method"] = "no submitted authors extracted"
        return result
    if not metadata:
        result["method"] = "no metadata authors"
        return result

    result["first_author_ok"] = surname_match(submitted[0], metadata[0])

    if uses_et_al:
        matched = sum(
            1
            for submitted_name, metadata_name in zip(submitted, metadata)
            if surname_match(submitted_name, metadata_name)
        )
        result["matched_count"] = matched
        result["submitted_coverage"] = matched / len(submitted)
        result["metadata_coverage"] = matched / len(metadata)
        result["ok"] = bool(
            result["first_author_ok"]
            and matched == len(submitted)
            and len(metadata) >= len(submitted)
        )
        result["method"] = "et al. named-author prefix" if result["ok"] else "et al. author mismatch"
        return result

    unmatched_metadata = set(range(len(metadata)))
    matched = 0
    for submitted_name in submitted:
        match_index = next(
            (
                index
                for index in unmatched_metadata
                if surname_match(submitted_name, metadata[index])
            ),
            None,
        )
        if match_index is not None:
            unmatched_metadata.remove(match_index)
            matched += 1

    submitted_coverage = matched / len(submitted)
    metadata_coverage = matched / len(metadata)
    result.update(
        {
            "matched_count": matched,
            "submitted_coverage": submitted_coverage,
            "metadata_coverage": metadata_coverage,
        }
    )

    if len(submitted) == len(metadata) == 1:
        result["ok"] = result["first_author_ok"]
        result["method"] = "single author" if result["ok"] else "single-author mismatch"
        return result

    result["ok"] = bool(
        result["first_author_ok"]
        and submitted_coverage >= 0.8
        and metadata_coverage >= 0.8
    )
    result["method"] = "full author list" if result["ok"] else "full-list mismatch"
    return result


def author_comparison_fields(
    submitted_authors: list[str],
    metadata_authors: list[str],
    *,
    uses_et_al: bool,
    comparison: dict | None = None,
) -> dict:
    comparison = comparison or compare_author_lists(
        submitted_authors,
        metadata_authors,
        uses_et_al=uses_et_al,
    )
    return {
        "Extracted Authors": ", ".join(submitted_authors),
        "Metadata Authors": ", ".join(metadata_authors),
        "Uses et al.": bool(uses_et_al),
        "Author match method": comparison["method"],
        "Author match count": int(comparison["matched_count"]),
        "Submitted author count": int(comparison["submitted_count"]),
        "Metadata author count": int(comparison["metadata_count"]),
        "Author submitted coverage": round(comparison["submitted_coverage"], 3),
        "Author metadata coverage": round(comparison["metadata_coverage"], 3),
        "First author matches": bool(comparison["first_author_ok"]),
    }

def title_similarity(a: str, b: str) -> float:
    a2 = re.sub(r"\s+", " ", (a or "").strip())
    b2 = re.sub(r"\s+", " ", (b or "").strip())
    return similar(a2, b2)


def normalize_title_for_match(value: str) -> str:
    """Normalize title text for conservative containment comparisons."""
    text = html.unescape(str(value or ""))
    text = strip_accents(text).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def title_is_distinctive_enough(normalized_title: str) -> bool:
    """Reject short/generic strings as containment evidence."""
    words = normalized_title.split()
    return len(normalized_title) >= 24 and len(words) >= 2


def evaluate_title_match(
    extracted_title: str,
    metadata_title: str,
    full_reference: str,
    *,
    similarity_threshold: float = 0.75,
) -> tuple[bool, float, str, float]:
    """Return title match, direct similarity, method, and confidence.

    Direct similarity remains the first choice. If parsing shortened or
    distorted the student title, a sufficiently distinctive trusted metadata
    title may validate against the extracted title or the full reference.
    """
    direct_similarity = title_similarity(extracted_title, metadata_title)
    if extracted_title and metadata_title and direct_similarity >= similarity_threshold:
        return True, direct_similarity, "extracted-title similarity", direct_similarity

    extracted_norm = normalize_title_for_match(extracted_title)
    metadata_norm = normalize_title_for_match(metadata_title)
    reference_norm = normalize_title_for_match(full_reference)

    if title_is_distinctive_enough(metadata_norm):
        if metadata_norm in extracted_norm:
            return True, direct_similarity, "metadata title found in extracted title", 1.0
        if metadata_norm in reference_norm:
            return True, direct_similarity, "metadata title found in full reference", 1.0

    if title_is_distinctive_enough(extracted_norm) and extracted_norm in metadata_norm:
        coverage = len(extracted_norm) / max(1, len(metadata_norm))
        if coverage >= 0.60:
            return True, direct_similarity, "extracted title found in metadata title", max(0.90, coverage)

    return False, direct_similarity, "no title match", direct_similarity

# --- volume/issue/pages parsing & comparison ---

def extract_vol_issue_pages(ref: str):
    s = ref
    m_vi = re.search(r"\b(\d{1,4})\s*\(\s*([A-Za-z0-9\-]+)\s*\)", s)
    vol = m_vi.group(1) if m_vi else ""
    issue = m_vi.group(2) if m_vi else ""
    if issue and YEAR_TOKEN_RE.fullmatch(issue):
        # In styles such as ``vol. 42 (2015a)``, the parenthesized token is
        # the publication year rather than an issue number.
        issue = ""

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

    year_any = re.compile(r"\b(?:1[89]\d{2}|20\d{2})[a-z]?\b", re.I)

    vancouver_author_body = (
        r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’`\-]{1,80}"
        r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’`\-]{1,40}){0,3}"
        r"\s+[A-Z][A-Z.\-]{0,8},"
    )
    vancouver_author = re.compile(rf"^{vancouver_author_body}", re.UNICODE)
    bare_numbered_marker = re.compile(
        rf"^\s*\d{{1,3}}\s+(?={vancouver_author_body})",
        re.UNICODE,
    )
    inline_numbered_reference = re.compile(
        rf"(?<=[.!?])\s+(?:\[\d{{1,3}}\]|\d{{1,3}}[.)]?)\s+"
        rf"(?={vancouver_author_body})",
        re.UNICODE,
    )

    # PDF text extraction can collapse a numbered Vancouver-style list into
    # one paragraph: "...4106. 6 Strickler JH, ...". Insert a boundary only
    # when terminal punctuation, a small list number, and an author pattern
    # all agree. Volume, issue, year and page numbers do not meet this gate.
    text = inline_numbered_reference.sub("\n", text)
    raw_lines = re.split(r"\r\n?|\n", text)

    personal_author = re.compile(
        r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’`\-]{1,80},\s*"
        r"(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’`\-]*|[A-Z](?:\.|\b))",
        re.UNICODE,
    )
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

    def looks_like_author_start(line: str) -> bool:
        if personal_author.match(line) or vancouver_author.match(line):
            return True
        return bool(
            re.match(
                r"^(?:The\s+)?[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9&'’`\-()./]+"
                r"(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9&'’`\-()./]+){0,10}",
                line,
                flags=re.UNICODE,
            )
        )

    def looks_like_strong_start(line: str) -> bool:
        if not line:
            return False
        if noise_line.match(line):
            return False
        return bool(
            ((personal_author.match(line) or vancouver_author.match(line)) and year_any.search(line))
            or entity_paren_year.match(line)
        )

    def looks_like_weak_start(line: str) -> bool:
        if not line or noise_line.match(line):
            return False
        return bool(
            (author_or_org_comma.match(line) and year_any.search(line))
            or entity_comma_year.match(line)
        )

    def looks_complete(ref: str) -> bool:
        identifier_or_url = bool(
            extract_doi(ref)
            or extract_pmcid(ref)
            or re.search(r"\bPMID:\s*\d+\b", ref, flags=re.I)
            or extract_urls(ref)
            or re.search(r"\b(?:Accessed|Assessed)(?: date)?\b", ref, flags=re.I)
        )
        if identifier_or_url:
            return True
        if not year_any.search(ref):
            return False
        return bool(
            re.search(r"\bpp?\.\s*\d+", ref, flags=re.I)
            or re.search(r"\b\d+\s*[-–—]\s*\d+\b", ref)
            or re.search(r"\b\d+\s*\(\s*[A-Za-z0-9\-]+\s*\)", ref)
        )

    refs: list[str] = []
    current: list[str] = []
    blank_before = False

    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped:
            blank_before = True
            continue

        standard_marker_match = LEADING_LIST_MARKER_RE.match(stripped)
        bare_marker_match = bare_numbered_marker.match(stripped)
        marker_match = standard_marker_match or bare_marker_match
        line = (
            stripped[bare_marker_match.end():].strip()
            if bare_marker_match
            else strip_leading_list_marker(stripped)
        )
        if not line:
            blank_before = True
            continue

        current_text = " ".join(current).strip()
        current_has_year = bool(year_any.search(current_text))
        explicit_start = bool(marker_match and looks_like_author_start(line))
        strong_start = looks_like_strong_start(line)
        weak_start = looks_like_weak_start(line)

        starts_new_reference = bool(
            current
            and (
                explicit_start
                or (strong_start and (current_has_year or blank_before))
                or (weak_start and blank_before and looks_complete(current_text))
                or (
                    looks_like_author_start(line)
                    and looks_complete(current_text)
                )
            )
        )

        if starts_new_reference:
            refs.append(current_text)
            current = [line]
        else:
            current.append(line)
        blank_before = False

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
    # A derived DOI is a metadata-discovery aid, not evidence supplied by the
    # student. Keep the flag for diagnostics but deliberately award no points.
    if author_ok:       score += 2
    if title_ok:        score += 2
    if year_ok:         score += 2
    if journal_ok:      score += 1
    if vol_ok:          score += 1
    if issue_ok:        score += 1
    if pages_ok:        score += 1
    if shape_ok:        score += 1
    return score


def collect_confirmed_metadata_conflicts(
    field_checks: dict[str, tuple[bool, bool, bool]],
) -> list[str]:
    """Return fields where both sides supplied a value and comparison failed.

    A missing student field or missing metadata field is deliberately not a
    contradiction. This keeps incomplete-but-genuine references out of the
    major-conflict rules and provides an extensible place for full-author-list
    comparisons later.
    """
    return [
        field
        for field, (submitted_present, metadata_present, matches) in field_checks.items()
        if submitted_present and metadata_present and not matches
    ]

def score_result(
    year_ok, author_ok, journal_ok, *,
    doi_explicit_ok=False, doi_derived_ok=False,
    ref="", shape_ok=False, has_url=False, had_crossref_candidate=False,
    title_ok=False, vol_ok=False, issue_ok=False, pages_ok=False,
    doi_invalid=False, manual_review_web=False,
    metadata_conflicts=None, strong_identity_match=False,
    author_conflict=False,
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

    if doi_explicit_ok and author_conflict and (not title_ok):
        return (
            "❌ possible falsification",
            "DOI resolves but both the submitted author list and title mismatch "
            "(likely wrong DOI or fabricated pairing)",
            weighted_score
        )

    if doi_explicit_ok and author_conflict:
        return (
            "⚠ Suspicious",
            "DOI resolves but the submitted author list conflicts with trusted metadata",
            weighted_score,
        )

    # Apply confirmed contradiction patterns before any rule based on the
    # positive score. The identity gate prevents a loose Crossref candidate
    # from making an unrelated student reference look falsified.
    conflicts = list(metadata_conflicts or [])
    # These rules apply when the DOI was discovered by the validator. A DOI
    # explicitly supplied by the student remains governed by the separate DOI
    # verification rules above/below; this avoids treating parser uncertainty
    # in optional fields as stronger evidence than a verified identifier.
    if (not doi_explicit_ok) and strong_identity_match and len(conflicts) >= 3:
        return (
            "❌ possible falsification",
            "major metadata conflict: title and author identify the work, but "
            + ", ".join(conflicts)
            + " contradict the trusted record",
            weighted_score,
        )

    if (not doi_explicit_ok) and strong_identity_match and len(conflicts) == 2:
        return (
            "⚠ Suspicious",
            "multiple metadata conflicts: title and author identify the work, but "
            + " and ".join(conflicts)
            + " contradict the trusted record",
            weighted_score,
        )

    if doi_explicit_ok:
        id_match = title_ok
        biblio_one = any([year_ok, journal_ok, vol_ok, issue_ok, pages_ok])
        if id_match and biblio_one:
            return "✅ Real", "", weighted_score

    # A discovered DOI can populate the comparison flags, but the reference
    # must pass the ordinary DOI-less metadata rule to be marked Real.
    if (not doi_explicit_ok) and year_ok and author_ok and journal_ok:
        return "✅ Real", "", weighted_score

    reasons = []
    if not doi_explicit_ok: reasons.append("no DOI supplied")
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

def choose_best_crossref_item(
    ref,
    items,
    first_author,
    year,
    ref_journal,
    title,
    submitted_authors=None,
    submitted_uses_et_al=False,
):
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

    y_ref, _ = parse_year_token(year)

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
        fams = metadata_author_surnames(it.get("author", []))

        title_sim = similar(title, it_title) if (title and it_title) else 0.0
        year_ok = bool(y_ref is not None and it_year is not None and abs(it_year - y_ref) <= tol)
        if submitted_authors:
            auth_ok = compare_author_lists(
                submitted_authors,
                fams,
                uses_et_al=submitted_uses_et_al,
            )["ok"]
        else:
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
    submitted_authors = extract_author_surnames(ref_clean)
    submitted_uses_et_al = reference_uses_et_al(ref_clean)
    first_author = submitted_authors[0] if submitted_authors else ""
    year = extract_year(ref_clean)
    year_value, _ = parse_year_token(year)
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
    ai_url_flag = has_chatgpt_utm_source(ref)
    ai_url_notes = AI_URL_NOTE if ai_url_flag else ""

    trusted_web = bool(primary_domain and domain_is_trusted(primary_domain))
    manual_review_web = looks_like_org_web_source(ref, primary_domain=primary_domain)

    ref_vol, ref_issue, ref_pages, ref_pstart, ref_pend = extract_vol_issue_pages(ref_clean)

    crossref_doi = crossref_journal = crossref_year = ""
    pubmed_id = pubmed_journal = pubmed_year = ""
    metadata_title = ""
    cr_vol = cr_issue = cr_pages = ""
    title_similarity_score = 0.0
    title_match_confidence = 0.0
    title_match_method = "no metadata title"

    metadata_authors = []
    author_comparison = compare_author_lists(
        submitted_authors,
        metadata_authors,
        uses_et_al=submitted_uses_et_al,
    )

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
            **author_comparison_fields(
                submitted_authors,
                metadata_authors,
                uses_et_al=submitted_uses_et_al,
                comparison=author_comparison,
            ),
            "journal_ok": False,
            "Validation Result": MANUAL_REVIEW_LABEL,
            "Raw Result": MANUAL_REVIEW_LABEL,
            "Notes": "URL detected; organisational/report/dataset-style source; manual review recommended",
            "AI URL flag": ai_url_flag,
            "AL notes": ai_url_notes,
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
            **author_comparison_fields(
                submitted_authors,
                metadata_authors,
                uses_et_al=submitted_uses_et_al,
                comparison=author_comparison,
            ),
            "journal_ok": False,
            "Validation Result": label,
            "Raw Result": label,
            "Notes": "URL detected; treated as non-academic web source",
            "AI URL flag": ai_url_flag,
            "AL notes": ai_url_notes,
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
                metadata_authors = metadata_author_surnames(item.get("author", []))
                it_title = (item.get("title") or [""])[0]
                metadata_title = it_title

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

            resolved_identifier = bool(doi_ok and doi_org_ok)
            if doi_source == "heuristic":
                doi_derived_ok = resolved_identifier
                doi_explicit_ok = False
            else:
                doi_explicit_ok = resolved_identifier
            doi_invalid = bool(
                doi_source != "heuristic" and (not doi_ok) and (not doi_org_ok)
            )

            if item:
                if year_value is not None and crossref_year.isdigit():
                    y_cr = int(crossref_year)
                    tol = 1 if year_value <= 1990 else 2
                    year_ok = abs(y_cr - year_value) <= tol

                author_comparison = compare_author_lists(
                    submitted_authors,
                    metadata_authors,
                    uses_et_al=submitted_uses_et_al,
                )
                author_ok = author_comparison["ok"]
                journal_ok = journals_match(ref_journal, crossref_journal)
                title_threshold = 0.78 if (year_value is not None and year_value <= 1990) else 0.75
                title_ok, title_similarity_score, title_match_method, title_match_confidence = evaluate_title_match(
                    title,
                    it_title,
                    ref_clean,
                    similarity_threshold=title_threshold,
                )

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
                metadata_title = str(summary.get("title") or "")
                cr_vol = str(summary.get("volume") or "").strip()
                cr_issue = str(summary.get("issue") or "").strip()
                cr_pages = str(summary.get("pages") or summary.get("elocationid") or "").strip()
                m_year = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(pubmed_year))
                year_ok = bool(year_value is not None and m_year and year_value == int(m_year.group(1)))

                metadata_authors = metadata_author_surnames(summary.get("authors", []))
                author_comparison = compare_author_lists(
                    submitted_authors,
                    metadata_authors,
                    uses_et_al=submitted_uses_et_al,
                )
                author_ok = author_comparison["ok"]
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
                metadata_title = str(summary.get("title") or "")
                cr_vol = str(summary.get("volume") or "").strip()
                cr_issue = str(summary.get("issue") or "").strip()
                cr_pages = str(summary.get("pages") or summary.get("elocationid") or "").strip()
                m_year = re.search(r"\b(1[89]\d{2}|20\d{2})\b", pubmed_year)
                year_ok = bool(year_value is not None and m_year and year_value == int(m_year.group(1)))

                metadata_authors = metadata_author_surnames(summary.get("authors") or [])
                author_comparison = compare_author_lists(
                    submitted_authors,
                    metadata_authors,
                    uses_et_al=submitted_uses_et_al,
                )
                author_ok = author_comparison["ok"]
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
                    first_pass_item, why = choose_best_crossref_item(
                        ref_clean,
                        items,
                        first_author,
                        year,
                        ref_journal,
                        title,
                        submitted_authors=submitted_authors,
                        submitted_uses_et_al=submitted_uses_et_al,
                    )
                    if first_pass_item:
                        item = first_pass_item
                        crossref_doi = item.get("DOI", "") or ""
                        crossref_journal = (item.get("container-title") or [""])[0]
                        iss = item.get("issued", {}).get("date-parts", [[None]])[0][0]
                        crossref_year = str(iss or "")
                        metadata_authors = metadata_author_surnames(item.get("author", []))
                        it_title = (item.get("title") or [""])[0]
                        metadata_title = it_title

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

                        if year_value is not None and crossref_year.isdigit():
                            y_cr = int(crossref_year)
                            year_ok = abs(y_cr - year_value) <= (1 if year_value <= 1990 else 2)
                        author_comparison = compare_author_lists(
                            submitted_authors,
                            metadata_authors,
                            uses_et_al=submitted_uses_et_al,
                        )
                        author_ok = author_comparison["ok"]
                        journal_ok = journals_match(ref_journal, crossref_journal)
                        title_threshold = 0.78 if (year_value is not None and year_value <= 1990) else 0.75
                        title_ok, title_similarity_score, title_match_method, title_match_confidence = evaluate_title_match(
                            title,
                            it_title,
                            ref_clean,
                            similarity_threshold=title_threshold,
                        )

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
                        item2, why2 = choose_best_crossref_item(
                            ref_clean,
                            items2,
                            first_author,
                            year,
                            ref_journal,
                            title,
                            submitted_authors=submitted_authors,
                            submitted_uses_et_al=submitted_uses_et_al,
                        )
                        if item2:
                            item = item2
                            crossref_doi = item.get("DOI", "") or ""
                            crossref_journal = (item.get("container-title") or [""])[0]
                            iss = item.get("issued", {}).get("date-parts", [[None]])[0][0]
                            crossref_year = str(iss or "")
                            metadata_authors = metadata_author_surnames(item.get("author", []))
                            it_title = (item.get("title") or [""])[0]
                            metadata_title = it_title

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

                            if year_value is not None and crossref_year.isdigit():
                                y_cr = int(crossref_year)
                                year_ok = abs(y_cr - year_value) <= (1 if year_value <= 1990 else 2)
                            author_comparison = compare_author_lists(
                                submitted_authors,
                                metadata_authors,
                                uses_et_al=submitted_uses_et_al,
                            )
                            author_ok = author_comparison["ok"]
                            journal_ok = journals_match(ref_journal, crossref_journal)
                            title_threshold = 0.78 if (year_value is not None and year_value <= 1990) else 0.75
                            title_ok, title_similarity_score, title_match_method, title_match_confidence = evaluate_title_match(
                                title,
                                it_title,
                                ref_clean,
                                similarity_threshold=title_threshold,
                            )

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
            **author_comparison_fields(
                submitted_authors,
                metadata_authors,
                uses_et_al=submitted_uses_et_al,
                comparison=author_comparison,
            ),
            "journal_ok": journal_ok,
            "Validation Result": base_result,
            "Raw Result": base_result,
            "Notes": f"lookup error: {e}",
            "AI URL flag": ai_url_flag,
            "AL notes": ai_url_notes,
            "Domain": primary_domain,
            "Domains": domains_joined,
            "doi_explicit_ok": doi_explicit_ok,
            "doi_derived_ok": doi_derived_ok,
        }

    metadata_year_value, _ = parse_year_token(crossref_year or pubmed_year)
    metadata_journal = crossref_journal or pubmed_journal
    author_conflict = bool(
        submitted_authors and metadata_authors and not author_comparison["ok"]
    )
    metadata_conflicts = collect_confirmed_metadata_conflicts(
        {
            "authors": (bool(submitted_authors), bool(metadata_authors), author_ok),
            "year": (year_value is not None, metadata_year_value is not None, year_ok),
            "journal": (bool(ref_journal), bool(metadata_journal), journal_ok),
            "volume": (bool(ref_vol), bool(cr_vol), vol_ok),
            "issue": (bool(ref_issue), bool(cr_issue), issue_ok),
            "pages": (bool(ref_pages or ref_pstart or ref_pend), bool(cr_pages), pages_ok),
        }
    )
    strong_identity_match = bool(
        author_ok and metadata_title and title_ok and title_match_confidence >= 0.90
    )

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
        manual_review_web=manual_review_web,
        metadata_conflicts=metadata_conflicts,
        strong_identity_match=strong_identity_match,
        author_conflict=author_conflict,
    )

    if doi_invalid:
        reason = (reason + ("; " if reason else "") + "DOI present but invalid (no Crossref and no doi.org resolution)").strip("; ")

    if doi_val and not doi_explicit_ok and not doi_derived_ok and not doi_invalid:
        reason = (reason + ("; " if reason else "") + "DOI present but doi.org did not resolve/redirect").strip("; ")

    if doi_derived_ok:
        reason = (
            reason
            + ("; " if reason else "")
            + "derived DOI used for metadata only; no DOI points awarded"
        ).strip("; ")

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

    if ai_url_flag:
        reason = (reason + ("; " if reason else "") + AI_URL_NOTE).strip("; ")

    return {
        "Reference": ref,
        "Extracted DOI": (doi_val or ""),
        "DOI Source": doi_source or "",
        "Crossref DOI": (crossref_doi or "").rstrip(".,;:)"),
        "Crossref Journal": crossref_journal,
        "Crossref Year": crossref_year,
        "Extracted Title": title,
        "Metadata Title": metadata_title,
        "Extracted Year": year,
        "Extracted Journal": ref_journal,
        "Extracted Volume": ref_vol,
        "Extracted Issue": ref_issue,
        "Extracted Pages": ref_pages,
        "Metadata Volume": cr_vol,
        "Metadata Issue": cr_issue,
        "Metadata Pages": cr_pages,
        "PubMed ID": pubmed_id,
        "PubMed Journal": pubmed_journal,
        "PubMed Year": pubmed_year,
        "Score": int(weighted_score),
        "Source URL": source_url,
        "doi_ok": doi_ok,
        "year_ok": year_ok,
        "author_ok": author_ok,
        **author_comparison_fields(
            submitted_authors,
            metadata_authors,
            uses_et_al=submitted_uses_et_al,
            comparison=author_comparison,
        ),
        "journal_ok": journal_ok,
        "Validation Result": result,
        "Raw Result": result,
        "Notes": reason,
        "AI URL flag": ai_url_flag,
        "AL notes": ai_url_notes,
        "Domain": primary_domain,
        "Domains": domains_joined,
        "doi_explicit_ok": doi_explicit_ok,
        "doi_derived_ok": doi_derived_ok,
        "title_ok": title_ok,
        "title_similarity": round(title_similarity_score, 3),
        "title_match_confidence": round(title_match_confidence, 3),
        "title_match_method": title_match_method,
        "strong_identity_match": strong_identity_match,
        "vol_ok": vol_ok,
        "issue_ok": issue_ok,
        "pages_ok": pages_ok,
        "metadata_conflict_count": len(metadata_conflicts),
        "metadata_conflicts": ", ".join(metadata_conflicts),
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
    if "AI URL flag" not in export_df.columns:
        export_df["AI URL flag"] = False
    if "Source URL" not in export_df.columns:
        export_df["Source URL"] = ""

    if view == "Condensed":
        export_df["DOI score"] = export_df.apply(compute_doi_score, axis=1)

        main_cols = ["Ref #", "Validation Result", "Score", "DOI score", "AI URL flag"]
        if include_review:
            main_cols += ["Is Review"]

        main_cols += ["Manual review", "AL notes", "Notes", "Source URL", "Reference"]
        main = export_df[[c for c in main_cols if c in export_df.columns]].copy()

    else:
        base_order = ["Ref #", "Validation Result", "Score"]

        if include_review:
            base_order += ["Is Review", "Review Source", "Review Notes"]

        base_order += [
            "Manual review", "AI URL flag", "AL notes",
            "Extracted Authors", "Metadata Authors", "Author match method",
            "Author match count", "Submitted author count", "Metadata author count",
            "Reference", "Domain", "Domains", "Source URL",
            "Extracted DOI", "DOI Source", "Crossref DOI", "Crossref Journal", "Crossref Year",
            "PubMed ID", "PubMed Journal", "PubMed Year", "Notes"
        ]

        debug_order = [
            "doi_explicit_ok", "doi_derived_ok", "year_ok", "author_ok", "journal_ok",
            "Uses et al.", "First author matches", "Author submitted coverage",
            "Author metadata coverage",
            "title_ok", "title_similarity", "title_match_confidence", "title_match_method",
            "strong_identity_match", "vol_ok", "issue_ok", "pages_ok", "metadata_conflict_count",
            "metadata_conflicts",
        ]
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
        | export_df["AI URL flag"].fillna(False).astype(bool)
    ].copy()

    if view == "Condensed":
        issues["DOI score"] = issues.apply(compute_doi_score, axis=1)

        issue_cols = ["Ref #", "Validation Result", "Score", "DOI score", "AI URL flag"]
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
                if col in ("Reference", "Notes", "AL notes", "Review Notes", "Source URL"):
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
            "Domains", "Journal", "PubMed Journal", "Crossref Journal", "Review Source", "Source URL"
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

st.set_page_config(
    page_title="Agent Ref — Beta",
    page_icon="🐢",
    layout="wide",
    initial_sidebar_state="expanded",
)

RESULTS_GUIDE_MD = """
Agent Ref marks a reference **Real** only when it passes a verification rule.
The score describes the strength of the evidence, but it does not decide the
result by itself.

- **Real:** A supplied DOI resolves and key details agree, or a DOI-free record
  has a strong match across trusted bibliographic metadata.
- **Manual review:** The source looks like a report, data set or organizational
  webpage that cannot be judged like a journal article.
- **Web source:** The item is a general webpage rather than a conventional
  academic publication.
- **Suspicious:** Some evidence agrees, but the reference is incomplete,
  unverifiable or contains a meaningful conflict.
- **Possible falsification:** The reference has a failed identifier, a fabricated
  pairing or several confirmed conflicts.

Confirmed conflicts are considered before the score. Missing information is not
treated as a contradiction.
"""

AUTHORS_GUIDE_MD = """
Agent Ref extracts every surname supplied by the student and compares it with
Crossref, PubMed or PMC metadata.

- A complete list must have the same first author and at least **80% coverage in
  both directions**.
- With **et al.**, every author named before *et al.* must match the corresponding
  author in the trusted record.
- A resolved DOI with a conflicting author list is sent for review rather than
  being marked Real automatically.
"""

TITLES_GUIDE_MD = """
Agent Ref first compares the extracted title with the trusted title. If
punctuation or formatting caused a poor extraction, it checks whether a
distinctive trusted title appears in the extracted title or anywhere in the
complete submitted reference.

Short, generic titles cannot pass through this fallback. Straight or curly
single and double quotation marks are supported.
"""

st.markdown(
    """
    <style>
    :root {
        --ar-bg: #F4FBF9;
        --ar-surface: #FFFFFF;
        --ar-surface-soft: #EAF7F4;
        --ar-text: #102A2E;
        --ar-muted: #536B70;
        --ar-primary: #067A8F;
        --ar-primary-dark: #034E5B;
        --ar-neon: #00E5C7;
        --ar-mint: #71FFB2;
        --ar-lime: #EFFF6A;
        --ar-warning: #8A5700;
        --ar-warning-bg: #FFF3C4;
        --ar-danger: #9F241C;
        --ar-danger-bg: #FFE2DE;
        --ar-success: #086B4E;
        --ar-success-bg: #D9FBE8;
        --ar-border: #C9DDD9;
    }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(circle at 88% 3%, rgba(0, 229, 199, 0.13), transparent 25rem),
            var(--ar-bg);
        color: var(--ar-text);
    }

    [data-testid="stHeader"] { background: rgba(244, 251, 249, 0.82); }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #063F49 0%, #052F36 100%);
        border-right: 1px solid rgba(113, 255, 178, 0.28);
    }

    [data-testid="stSidebar"] * { color: #F5FFFC; }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
        color: #C5DFDB !important;
    }

    .block-container {
        max-width: 1180px;
        padding-top: 2.2rem;
        padding-bottom: 5rem;
    }

    .ar-hero {
        position: relative;
        overflow: hidden;
        padding: 2.1rem 2.3rem;
        margin-bottom: 1.8rem;
        border-radius: 28px;
        background: linear-gradient(130deg, #043C46 0%, #067A8F 72%, #0797A5 100%);
        color: white;
        box-shadow: 0 18px 46px rgba(3, 78, 91, 0.16);
    }

    .ar-hero::after {
        content: "";
        position: absolute;
        width: 210px;
        height: 210px;
        right: -55px;
        top: -85px;
        border-radius: 50%;
        background: var(--ar-lime);
        opacity: 0.92;
    }

    .ar-eyebrow {
        display: inline-flex;
        padding: 0.28rem 0.7rem;
        margin-bottom: 0.8rem;
        border: 1px solid rgba(255,255,255,0.35);
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 750;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }

    .ar-hero h1 {
        margin: 0;
        color: white;
        font-size: clamp(2.25rem, 5vw, 4rem);
        line-height: 1;
        letter-spacing: -0.045em;
    }

    .ar-hero p {
        max-width: 680px;
        margin: 0.9rem 0 0;
        color: #DFFBF6;
        font-size: 1.08rem;
    }

    .ar-section-kicker {
        margin: 2.2rem 0 0.25rem;
        color: var(--ar-primary);
        font-size: 0.78rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    .ar-count {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        box-sizing: border-box;
        height: 2.75rem;
        padding: 0.35rem 0.75rem;
        border: 1px solid var(--ar-border);
        border-radius: 999px;
        background: var(--ar-surface-soft);
        color: var(--ar-primary-dark);
        font-weight: 700;
        transform: translateY(-0.5rem);
    }

    div[data-testid="stColumn"]:has(.ar-count) [data-testid="stMarkdownContainer"] {
        display: flex;
        min-height: 2.75rem;
        align-items: center;
    }

    .ar-summary-card {
        box-sizing: border-box;
        height: 132px;
        padding: 1rem 1.05rem;
        border: 1px solid var(--ar-border);
        border-radius: 20px;
        background: var(--ar-surface);
        box-shadow: 0 8px 24px rgba(15, 61, 68, 0.07);
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
    }

    .ar-summary-card .value {
        display: block;
        margin-bottom: 0.18rem;
        color: var(--ar-text);
        font-size: 2rem;
        font-weight: 820;
        line-height: 1;
    }

    .ar-summary-card .label {
        color: var(--ar-muted);
        font-size: 0.86rem;
        font-weight: 650;
        line-height: 1.25;
    }

    .ar-summary-card.real { border-top: 5px solid var(--ar-mint); }
    .ar-summary-card.review { border-top: 5px solid var(--ar-primary); }
    .ar-summary-card.warning { border-top: 5px solid #F4C84B; }
    .ar-summary-card.danger { border-top: 5px solid #D94B43; }
    .ar-summary-card.total { border-top: 5px solid var(--ar-neon); }

    .st-key-summary_filters [data-testid="stButton"] button {
        box-sizing: border-box;
        height: 132px;
        padding: 1rem 1.05rem;
        border: 1px solid var(--ar-border);
        border-radius: 20px;
        background: var(--ar-surface);
        color: var(--ar-text);
        box-shadow: 0 8px 24px rgba(15, 61, 68, 0.07);
        align-items: flex-start;
        justify-content: flex-start;
        text-align: left;
        white-space: normal;
    }

    .st-key-summary_filters [data-testid="stButton"] button p {
        color: var(--ar-muted);
        font-size: 0.86rem;
        font-weight: 650;
        line-height: 1.25;
        text-align: left;
        white-space: normal;
    }

    .st-key-summary_filters [data-testid="stButton"] button strong {
        display: block;
        margin-bottom: 0.2rem;
        color: var(--ar-text);
        font-size: 2rem;
        font-weight: 820;
        line-height: 1;
    }

    .st-key-summary_filters [data-testid="stButton"] button[kind="primary"] {
        border-color: var(--ar-primary);
        background: var(--ar-surface-soft);
        color: var(--ar-text);
        box-shadow: 0 0 0 3px rgba(0, 229, 199, 0.18),
                    0 8px 24px rgba(15, 61, 68, 0.07);
    }

    .st-key-summary_all button { border-top: 5px solid var(--ar-neon) !important; }
    .st-key-summary_real button { border-top: 5px solid var(--ar-mint) !important; }
    .st-key-summary_review button { border-top: 5px solid var(--ar-primary) !important; }
    .st-key-summary_suspicious button { border-top: 5px solid #F4C84B !important; }
    .st-key-summary_danger button { border-top: 5px solid #D94B43 !important; }

    .ar-reference-card {
        padding: 1.15rem 1.25rem;
        margin: 0.5rem 0 1rem;
        border: 1px solid var(--ar-border);
        border-left: 6px solid var(--ar-primary);
        border-radius: 18px;
        background: var(--ar-surface);
        box-shadow: 0 8px 24px rgba(15, 61, 68, 0.06);
    }

    .ar-reference-card.real { border-left-color: var(--ar-success); }
    .ar-reference-card.warning { border-left-color: #D69A00; }
    .ar-reference-card.danger { border-left-color: var(--ar-danger); }

    .ar-reference-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
        margin-bottom: 0.75rem;
    }

    .ar-chip {
        display: inline-flex;
        padding: 0.3rem 0.65rem;
        border-radius: 999px;
        background: var(--ar-surface-soft);
        color: var(--ar-primary-dark);
        font-size: 0.82rem;
        font-weight: 750;
    }

    .ar-reference-text { color: var(--ar-text); line-height: 1.55; }

    .ar-guide {
        padding: 1.3rem 1.45rem;
        margin-bottom: 1rem;
        border: 1px solid var(--ar-border);
        border-radius: 20px;
        background: rgba(255,255,255,0.82);
    }

    .ar-sidebar-brand {
        margin: 0.35rem 0 1.2rem;
        font-size: 1.35rem;
        font-weight: 850;
        letter-spacing: -0.03em;
    }

    .ar-sidebar-brand span { color: var(--ar-lime); }

    [data-testid="stSidebar"] div[role="radiogroup"] {
        gap: 0.3rem;
        margin: 1rem 0 1.4rem;
    }

    [data-testid="stSidebar"] div[role="radiogroup"] label {
        box-sizing: border-box;
        width: 100%;
        padding: 0.66rem 0.75rem;
        border: 1px solid transparent;
        border-radius: 12px;
        transition: background 120ms ease, border-color 120ms ease;
    }

    [data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background: rgba(0, 229, 199, 0.12);
    }

    [data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        border-color: rgba(113, 255, 178, 0.55);
        background: rgba(0, 229, 199, 0.18);
    }

    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextInput"] input {
        border-color: var(--ar-border);
        border-radius: 16px;
        background: white;
        color: var(--ar-text) !important;
        caret-color: var(--ar-primary);
    }

    div[data-testid="stTextArea"] textarea::placeholder,
    div[data-testid="stTextInput"] input::placeholder {
        color: #789095 !important;
        opacity: 1;
    }

    div[data-testid="stTextArea"] textarea:focus,
    div[data-testid="stTextInput"] input:focus {
        border-color: var(--ar-primary);
        box-shadow: 0 0 0 3px rgba(0, 229, 199, 0.18);
    }

    .stButton > button,
    .stDownloadButton > button,
    [data-testid="stLinkButton"] a {
        min-height: 2.75rem;
        border-radius: 999px;
        font-weight: 750;
    }

    .stButton > button[kind="primary"],
    .stDownloadButton > button {
        border: 1px solid var(--ar-primary-dark);
        background: var(--ar-primary);
        color: white;
    }

    .stButton > button[kind="secondary"] {
        border: 1px solid #8CB8B2;
        background: white;
        color: var(--ar-primary-dark);
    }

    [data-testid="stLinkButton"] a {
        border-color: rgba(113, 255, 178, 0.45);
        background: rgba(0, 229, 199, 0.12);
        color: white !important;
    }

    [data-testid="stProgress"] > div > div > div > div {
        background: linear-gradient(90deg, var(--ar-neon), var(--ar-primary));
    }

    [data-testid="stDataFrame"] {
        overflow: hidden;
        border: 1px solid var(--ar-border);
        border-radius: 16px;
    }

    h1, h2, h3 { color: var(--ar-text); letter-spacing: -0.025em; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "sid" not in st.session_state:
    st.session_state["sid"] = str(uuid.uuid4())
if "validation_results" not in st.session_state:
    st.session_state["validation_results"] = []
if "last_checked_count" not in st.session_state:
    st.session_state["last_checked_count"] = 0

with st.sidebar:
    st.markdown(
        '<div class="ar-sidebar-brand">Agent Ref <span>beta</span></div>',
        unsafe_allow_html=True,
    )
    if "check_reviews" not in st.session_state:
        st.session_state["check_reviews"] = False
    check_reviews = st.toggle(
        f"Detect reviews: {'on' if st.session_state['check_reviews'] else 'off'}",
        key="check_reviews",
        help="Check PubMed publication types and title patterns. This can take longer.",
    )
    st.caption("Review detection will slow down the response time.")

    page_choice = st.radio(
        "Pages",
        [
            "Check references",
            "How results are decided",
            "How authors are checked",
            "How titles are checked",
        ],
        key="page_choice",
        label_visibility="collapsed",
    )

    with st.expander("Advanced"):
        debug_mode = st.checkbox(
            "Show technical details",
            value=False,
            help="Add internal validation flags to the all-references view and full downloads.",
        )

    st.link_button(
        "Open regex inspector",
        "https://agent-ref-regex-inspector.streamlit.app/",
        use_container_width=True,
    )

st.markdown(
    """
    <section class="ar-hero">
      <span class="ar-eyebrow">Beta test version</span>
      <h1>Agent Ref</h1>
      <p>Validate a reference list against academic databases.</p>
    </section>
    """,
    unsafe_allow_html=True,
)

if page_choice != "Check references":
    st.markdown('<div class="ar-section-kicker">Guide</div>', unsafe_allow_html=True)
    if page_choice == "How results are decided":
        st.header("How results are decided")
        with st.container(border=True):
            st.markdown(RESULTS_GUIDE_MD)
            st.markdown(
                """
                **Evidence score**

                - **4 points:** A DOI supplied by the student resolves.
                - **2 points each:** The author list, title and year match.
                - **1 point each:** The journal, volume, issue, pages and overall
                  reference structure match.

                A DOI found by Agent Ref retrieves metadata but adds no points.
                """
            )
    elif page_choice == "How authors are checked":
        st.header("How authors are checked")
        with st.container(border=True):
            st.markdown(AUTHORS_GUIDE_MD)
    else:
        st.header("How titles are checked")
        with st.container(border=True):
            st.markdown(TITLES_GUIDE_MD)

    st.markdown(
        """
        <div style="margin-top:3rem;padding-top:1rem;border-top:1px solid #C9DDD9;color:#536B70;font-size:.86rem">
          Agent Ref beta · Created by Mark Hintze ·
          <a href="mailto:FoxHin5431@users.noreply.github.com">FoxHin5431@users.noreply.github.com</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

def clear_ref_input():
    st.session_state["ref_input"] = ""
    st.session_state["validation_results"] = []
    st.session_state["last_checked_count"] = 0


if "ref_input" not in st.session_state:
    st.session_state["ref_input"] = ""

st.markdown('<div id="check-references"></div>', unsafe_allow_html=True)
st.markdown('<div class="ar-section-kicker">Reference check</div>', unsafe_allow_html=True)
st.header("Paste your references")
st.caption(
    "Paste a reference list from Word, a PDF or another document. "
    "Line breaks inside a reference are treated as spaces when possible."
)

user_input = st.text_area(
    "References",
    height=330,
    key="ref_input",
    placeholder=(
        "Example:\n"
        "Stern, C.D. and Piatkowska, A.M. (2015) 'Multiple roles of timing "
        "in somite formation.' Seminars in Cell & Developmental Biology, 42, pp. 134–139."
    ),
    label_visibility="collapsed",
)

ready_refs = []
if user_input.strip():
    try:
        ready_refs = split_references(user_input)
    except Exception:
        ready_refs = []

action_col, clear_col, count_col = st.columns([1.7, 1.7, 4.6], vertical_alignment="center")
with action_col:
    run_clicked = st.button(
        "Check references",
        type="primary",
        use_container_width=True,
    )
with clear_col:
    st.button("Clear", on_click=clear_ref_input, use_container_width=True)
with count_col:
    ready_label = "reference" if len(ready_refs) == 1 else "references"
    st.markdown(
        f'<div class="ar-count">{len(ready_refs)} {ready_label} ready to check</div>',
        unsafe_allow_html=True,
    )

if user_input.strip():
    warns = sanity_check(user_input)
    if warns:
        st.warning("Reference formatting may be inconsistent:\n\n- " + "\n- ".join(warns))

st.caption(
    "When you select Check references, Agent Ref records the submitted list, "
    "the result summary and any errors to help improve this beta. "
    "It does not collect personal user data."
)

def _utc_now():
    return datetime.now(timezone.utc)

def _log_run(*, input_text: str, split_refs: list[str], result_counts: dict, error_text: str | None):
    possible_secret_files = (
        Path.home() / ".streamlit" / "secrets.toml",
        Path.cwd() / ".streamlit" / "secrets.toml",
    )
    if not any(path.is_file() for path in possible_secret_files):
        return
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

# =========================
# Display helpers
# =========================

def compute_doi_score(row: pd.Series) -> int:
    """
    Simple numeric DOI score for the condensed table:
      2 = explicit DOI hard-resolved (Crossref + doi.org)
      0 = no student-supplied usable DOI (including a resolved derived DOI)
     -1 = DOI present but invalid (no Crossref and no doi.org)
    """
    extracted = str(row.get("Extracted DOI", "") or "").strip()
    derived = str(row.get("Crossref DOI", "") or "").strip()

    doi_present = bool(extracted or derived)

    if bool(row.get("doi_explicit_ok", False)):
        return 2
    if bool(row.get("doi_derived_ok", False)):
        return 0

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

def build_display_df(df: pd.DataFrame, *, view: str, include_review: bool) -> pd.DataFrame:
    df2 = df.copy()
    df2["DOI score"] = df2.apply(compute_doi_score, axis=1)
    df2 = add_link_columns(df2)

    if view == "Condensed":
        cols = ["Ref #", "Validation Result", "Score", "DOI score", "AI URL flag", "AL notes", "Notes", "Source link", "DOI link"]
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
        "AI URL flag",
        "AL notes",
        "Extracted Authors", "Metadata Authors", "Author match method",
        "Author match count", "Submitted author count", "Metadata author count",
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

    debug_cols = [
        "doi_explicit_ok", "doi_derived_ok", "year_ok", "author_ok", "journal_ok",
        "Uses et al.", "First author matches", "Author submitted coverage",
        "Author metadata coverage",
    ]
    if debug_mode:
        cols += debug_cols

    for c in cols:
        if c not in df2.columns:
            df2[c] = ""

    existing_cols = [c for c in cols if c in df2.columns]
    return df2[existing_cols].copy()

def _display_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return html.unescape(str(value))


def _part_status(matches: bool, submitted, metadata) -> str:
    submitted_text = _display_text(submitted).strip()
    metadata_text = _display_text(metadata).strip()
    if not submitted_text and not metadata_text:
        return "Not available"
    if not metadata_text:
        return "Not found"
    if not submitted_text:
        return "Metadata only"
    return "Matched" if bool(matches) else "Different"


def build_part_by_part_df(row: pd.Series) -> pd.DataFrame:
    submitted_doi = _display_text(row.get("Extracted DOI")).strip()
    metadata_doi = _display_text(row.get("Crossref DOI")).strip()
    if bool(row.get("doi_explicit_ok")):
        doi_status = "Matched"
        doi_explanation = "The DOI supplied in the reference resolves."
    elif bool(row.get("doi_derived_ok")):
        doi_status = "Metadata only"
        doi_explanation = "Agent Ref found this DOI; it did not add points to the score."
    elif submitted_doi:
        doi_status = "Different"
        doi_explanation = "The supplied DOI could not be confirmed."
    else:
        doi_status = "Not found"
        doi_explanation = "No DOI was supplied or recovered."

    submitted_authors = _display_text(row.get("Extracted Authors"))
    metadata_authors = _display_text(row.get("Metadata Authors"))
    matched_authors = int(row.get("Author match count") or 0)
    submitted_author_count = int(row.get("Submitted author count") or 0)
    author_method = _display_text(row.get("Author match method")) or "No comparison available"
    reference = _display_text(row.get("Reference"))
    submitted_title = _display_text(row.get("Extracted Title")) or extract_title(reference)
    metadata_title = _display_text(row.get("Metadata Title"))
    submitted_year = _display_text(row.get("Extracted Year")) or extract_year(reference)
    metadata_year = _display_text(row.get("Crossref Year") or row.get("PubMed Year"))
    submitted_journal = _display_text(row.get("Extracted Journal")) or extract_journal(reference)
    metadata_journal = _display_text(row.get("Crossref Journal") or row.get("PubMed Journal"))

    rows = [
        {
            "Part": "DOI",
            "Submitted reference": submitted_doi or "—",
            "Trusted record": metadata_doi or "—",
            "Status": doi_status,
            "What this means": doi_explanation,
        },
        {
            "Part": "Authors",
            "Submitted reference": submitted_authors or "—",
            "Trusted record": metadata_authors or "—",
            "Status": _part_status(row.get("author_ok"), submitted_authors, metadata_authors),
            "What this means": (
                f"{matched_authors} of {submitted_author_count} named authors matched. "
                f"Method: {author_method}."
            ),
        },
        {
            "Part": "Year",
            "Submitted reference": submitted_year or "—",
            "Trusted record": metadata_year or "—",
            "Status": _part_status(row.get("year_ok"), submitted_year, metadata_year),
            "What this means": "Letter suffixes such as 2025a use the four-digit year.",
        },
        {
            "Part": "Title",
            "Submitted reference": submitted_title or "—",
            "Trusted record": metadata_title or "—",
            "Status": _part_status(row.get("title_ok"), submitted_title, metadata_title),
            "What this means": (
                f"Method: {_display_text(row.get('title_match_method')) or 'Not checked'}. "
                f"Confidence: {_display_text(row.get('title_match_confidence')) or '—'}."
            ),
        },
        {
            "Part": "Journal",
            "Submitted reference": submitted_journal or "—",
            "Trusted record": metadata_journal or "—",
            "Status": _part_status(row.get("journal_ok"), submitted_journal, metadata_journal),
            "What this means": "Common abbreviations and punctuation are normalized.",
        },
        {
            "Part": "Volume",
            "Submitted reference": _display_text(row.get("Extracted Volume")) or "—",
            "Trusted record": _display_text(row.get("Metadata Volume")) or "—",
            "Status": _part_status(row.get("vol_ok"), row.get("Extracted Volume"), row.get("Metadata Volume")),
            "What this means": "Compared when both records provide a volume.",
        },
        {
            "Part": "Issue",
            "Submitted reference": _display_text(row.get("Extracted Issue")) or "—",
            "Trusted record": _display_text(row.get("Metadata Issue")) or "—",
            "Status": _part_status(row.get("issue_ok"), row.get("Extracted Issue"), row.get("Metadata Issue")),
            "What this means": "Compared when both records provide an issue.",
        },
        {
            "Part": "Pages",
            "Submitted reference": _display_text(row.get("Extracted Pages")) or "—",
            "Trusted record": _display_text(row.get("Metadata Pages")) or "—",
            "Status": _part_status(row.get("pages_ok"), row.get("Extracted Pages"), row.get("Metadata Pages")),
            "What this means": "Page ranges and article locators are accepted.",
        },
    ]
    return pd.DataFrame(rows)


def style_part_by_part(df: pd.DataFrame):
    colors = {
        "Matched": "background-color: #D9FBE8; color: #086B4E; font-weight: 700",
        "Different": "background-color: #FFE2DE; color: #9F241C; font-weight: 700",
        "Not found": "background-color: #FFF3C4; color: #704700; font-weight: 700",
        "Metadata only": "background-color: #DFF7F3; color: #034E5B; font-weight: 700",
        "Not available": "background-color: #EDF2F1; color: #536B70; font-weight: 700",
    }
    return df.style.map(lambda value: colors.get(str(value), ""), subset=["Status"])


def _result_tone(label: str) -> str:
    label_lower = (label or "").lower()
    if "possible falsification" in label_lower or "error" in label_lower:
        return "danger"
    if "suspicious" in label_lower or "manual review" in label_lower:
        return "warning"
    if "real" in label_lower:
        return "real"
    return "review"


if run_clicked:
    refs: list[str] = []
    result_counts: dict = {}
    error_text: str | None = None

    try:
        refs = ready_refs or split_references(user_input)
        total = len(refs)
        if not total:
            st.warning("No complete references were detected.")
        else:
            progress = st.progress(0, text=f"Preparing to check {total} references")

            async def process_all(_refs):
                results = []
                async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
                    for i, ref in enumerate(_refs, start=1):
                        progress.progress(
                            (i - 1) / total,
                            text=f"Checking reference {i} of {total}",
                        )
                        result = await validate_single_ref(
                            client,
                            ref,
                            debug_mode,
                            check_reviews=check_reviews,
                        )
                        result["Ref #"] = i
                        results.append(result)
                return results

            results = asyncio.run(process_all(refs))
            progress.progress(1.0, text=f"Finished checking {total} references")
            st.session_state["validation_results"] = results
            st.session_state["last_checked_count"] = total
            st.session_state["results_detect_reviews"] = check_reviews

            df_for_log = pd.DataFrame(results)
            result_counts = df_for_log["Validation Result"].value_counts().to_dict()

    except Exception as exc:
        error_text = str(exc)
        st.error(
            "Agent Ref could not finish this check. Your references are still "
            "in the input box, so you can try again."
        )
        if debug_mode:
            st.exception(exc)

    finally:
        _log_run(
            input_text=user_input,
            split_refs=refs,
            result_counts=result_counts,
            error_text=error_text,
        )


stored_results = st.session_state.get("validation_results", [])
if stored_results:
    df = pd.DataFrame(stored_results)
    counts = df["Validation Result"].value_counts()

    total_count = int(len(df))
    real_count = int(counts.get("✅ Real", 0))
    manual_count = int(counts.get(MANUAL_REVIEW_LABEL, 0))
    web_count = int(
        counts.get("📄 Web Source (trusted)", 0)
        + counts.get("📄 Web Source", 0)
    )
    suspicious_count = int(counts.get("⚠ Suspicious", 0))
    false_count = int(counts.get("❌ possible falsification", 0))
    error_count = int(counts.get("❌ Error", 0))

    st.markdown('<div class="ar-section-kicker">Results</div>', unsafe_allow_html=True)
    st.header(f"Summary for {total_count} references")

    summary_items = [
        ("all", total_count, "References checked"),
        ("real", real_count, "Real"),
        ("review", manual_count + web_count, "Manual review or web"),
        ("suspicious", suspicious_count, "Suspicious"),
        ("danger", false_count + error_count, "Possible falsification or error"),
    ]
    if "result_filter" not in st.session_state:
        st.session_state["result_filter"] = "all"
    available_filters = {
        filter_key for filter_key, value, _ in summary_items
        if filter_key == "all" or value > 0
    }
    if st.session_state["result_filter"] not in available_filters:
        st.session_state["result_filter"] = "all"

    def _set_result_filter(filter_key: str) -> None:
        st.session_state["result_filter"] = filter_key

    with st.container(key="summary_filters"):
        summary_columns = st.columns(len(summary_items))
        for column, (filter_key, value, label) in zip(summary_columns, summary_items):
            with column:
                st.button(
                    f"**{value}**  \n{label}",
                    key=f"summary_{filter_key}",
                    type=(
                        "primary"
                        if st.session_state["result_filter"] == filter_key
                        else "secondary"
                    ),
                    disabled=filter_key != "all" and value == 0,
                    use_container_width=True,
                    help=(
                        "Show every checked reference"
                        if filter_key == "all"
                        else f"Show {label.lower()} references"
                    ),
                    on_click=_set_result_filter,
                    args=(filter_key,),
                )

    st.caption(
        "Select a summary card to filter the reference dropdown below. "
        "Select References checked to show the full list."
    )

    ai_tracking_count = int(
        df.get("AI URL flag", pd.Series(dtype=bool))
        .fillna(False)
        .astype(bool)
        .sum()
    )
    if ai_tracking_count:
        st.info(
            f"AI tracking was found in {ai_tracking_count} submitted "
            f"{'URL' if ai_tracking_count == 1 else 'URLs'}. "
            "This advisory flag does not change the score."
        )

    st.subheader("Review one reference")
    active_filter = st.session_state.get("result_filter", "all")

    def _is_in_active_filter(row: pd.Series) -> bool:
        label = _display_text(row.get("Validation Result")).lower()
        if active_filter == "all":
            return True
        if active_filter == "real":
            return "real" in label and "manual" not in label
        if active_filter == "review":
            return "manual review" in label or "web source" in label
        if active_filter == "suspicious":
            return "suspicious" in label
        return "possible falsification" in label or "error" in label

    filtered_indices = [
        index for index in range(len(df))
        if _is_in_active_filter(df.iloc[index])
    ]
    filter_labels = {key: label for key, _, label in summary_items}
    if active_filter != "all":
        st.caption(
            f"Showing {len(filtered_indices)} of {total_count}: "
            f"{filter_labels[active_filter]}. Select References checked to clear the filter."
        )
    selected_index = st.selectbox(
        "Select a reference",
        options=filtered_indices,
        format_func=lambda index: (
            f"{int(df.iloc[index].get('Ref #', index + 1))} — "
            f"{_display_text(df.iloc[index].get('Validation Result')).replace('✅ ', '').replace('⚠ ', '').replace('❌ ', '')} — "
            f"{' '.join(_display_text(df.iloc[index].get('Reference')).split())[:95]}"
        ),
        label_visibility="collapsed",
    )
    selected = df.iloc[int(selected_index)]
    selected_label = _display_text(selected.get("Validation Result"))
    selected_score = int(selected.get("Score") or 0)
    selected_tone = _result_tone(selected_label)

    st.markdown(
        (
            f'<article class="ar-reference-card {selected_tone}">'
            '<div class="ar-reference-meta">'
            f'<span class="ar-chip">{html.escape(selected_label)}</span>'
            f'<span class="ar-chip">Evidence score: {selected_score} of 15</span>'
            "</div>"
            f'<div class="ar-reference-text">{html.escape(_display_text(selected.get("Reference")))}</div>'
            "</article>"
        ),
        unsafe_allow_html=True,
    )

    validator_notes = _display_text(selected.get("Notes"))
    st.markdown("**Validator notes**")
    st.info(validator_notes or "No additional validator notes were returned.")
    ai_notes = _display_text(selected.get("AL notes"))
    if ai_notes:
        st.warning(ai_notes)

    detail_tabs = ["Part by part", "All references", "Technical details"]
    tabs = st.tabs(detail_tabs)

    with tabs[0]:
        part_df = build_part_by_part_df(selected)
        visible_part_df = part_df[
            ["Part", "Submitted reference", "Trusted record", "Status"]
        ]
        st.dataframe(
            style_part_by_part(visible_part_df),
            use_container_width=True,
            hide_index=True,
            height=390,
            column_config={
                "Part": st.column_config.TextColumn("Part", width="small"),
                "Submitted reference": st.column_config.TextColumn(
                    "Submitted reference", width="large"
                ),
                "Trusted record": st.column_config.TextColumn(
                    "Trusted record", width="large"
                ),
                "Status": st.column_config.TextColumn("Status", width="small"),
            },
        )

    with tabs[1]:
        overview = df[
            ["Ref #", "Validation Result", "Score", "Reference", "Notes"]
        ].copy()
        overview.columns = [
            "Reference",
            "Result",
            "Evidence score",
            "Submitted reference",
            "Explanation",
        ]
        st.dataframe(
            overview,
            use_container_width=True,
            hide_index=True,
            height=430,
            column_config={
                "Reference": st.column_config.NumberColumn(
                    "Reference", width="small"
                ),
                "Result": st.column_config.TextColumn("Result", width="medium"),
                "Evidence score": st.column_config.NumberColumn(
                    "Evidence score", format="%d", width="small"
                ),
                "Submitted reference": st.column_config.TextColumn(
                    "Submitted reference", width="large"
                ),
                "Explanation": st.column_config.TextColumn(
                    "Explanation", width="large"
                ),
            },
        )

    with tabs[2]:
        st.caption(
            "These notes explain how each part of the selected reference is interpreted."
        )
        technical_explanations = part_df[
            ["Part", "Status", "What this means"]
        ]
        st.dataframe(
            style_part_by_part(technical_explanations),
            use_container_width=True,
            hide_index=True,
            height=390,
            column_config={
                "Part": st.column_config.TextColumn("Part", width="small"),
                "Status": st.column_config.TextColumn("Status", width="small"),
                "What this means": st.column_config.TextColumn(
                    "What this means", width="large"
                ),
            },
        )
        if debug_mode:
            st.markdown("**Internal validation flags**")
            debug_columns = [
                "Ref #", "doi_explicit_ok", "doi_derived_ok", "year_ok",
                "author_ok", "journal_ok", "title_ok", "vol_ok", "issue_ok",
                "pages_ok", "metadata_conflict_count", "metadata_conflicts",
                "title_match_method", "title_match_confidence",
            ]
            st.dataframe(
                df[[column for column in debug_columns if column in df.columns]],
                use_container_width=True,
                hide_index=True,
            )

    st.markdown('<div class="ar-section-kicker">Export</div>', unsafe_allow_html=True)
    st.header("Download results")
    with st.container(border=True):
        file_name_col, type_col, detail_col = st.columns([2.3, 1.25, 1.25])
        with file_name_col:
            prefix_input = st.text_input(
                "Optional file name",
                value=st.session_state.get("prefix", ""),
                placeholder="For example: cohort-a",
                help="This text is added to the start of the downloaded file name.",
            )
            st.session_state["prefix"] = prefix_input
        with type_col:
            export_type = st.selectbox("File type", ["Excel", "CSV"])
        with detail_col:
            export_detail = st.selectbox("Detail", ["Condensed", "Full"])

        prefix = sanitize_prefix(prefix_input)
        today = date.today().isoformat()
        include_review = bool(
            st.session_state.get("results_detect_reviews", False)
        )

        if export_type == "Excel":
            export_bytes = build_excel_workbook(
                df,
                debug_mode=debug_mode and export_detail == "Full",
                view=export_detail,
                include_review=include_review,
            )
            export_filename = (
                f"{prefix}validated_references_"
                f"{export_detail.lower()}_{today}.xlsx"
            )
            export_mime = (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            )
        else:
            export_df = build_display_df(
                df,
                view=export_detail,
                include_review=include_review,
            )
            export_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
            export_filename = (
                f"{prefix}validated_references_"
                f"{export_detail.lower()}_{today}.csv"
            )
            export_mime = "text/csv"

        st.download_button(
            f"Download {export_detail.lower()} {export_type}",
            export_bytes,
            export_filename,
            export_mime,
            use_container_width=True,
        )


st.markdown(
    """
    <div style="margin-top:3rem;padding-top:1rem;border-top:1px solid #C9DDD9;color:#536B70;font-size:.86rem">
      Agent Ref beta · Created by Mark Hintze ·
      <a href="mailto:FoxHin5431@users.noreply.github.com">FoxHin5431@users.noreply.github.com</a>
    </div>
    """,
    unsafe_allow_html=True,
)
