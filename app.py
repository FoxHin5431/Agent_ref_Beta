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
