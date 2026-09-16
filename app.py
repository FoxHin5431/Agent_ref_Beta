# Agent Ref interface. Validation lives in agent_ref_validator/core.py.
import streamlit as st
import os
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

from agent_ref_validator.core import *  # Shared validator public API
from agent_ref_validator.exports import build_excel_workbook, compute_doi_score

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
- **Unable to verify:** Parsing, lookup or metadata could not establish the source
  reliably. These references remain in the review queue.
- **Suspicious:** The retrieved record contains a meaningful contradiction.
- **Possible falsification:** An identifier points to different authors and title,
  or several publication details contradict an identified record.

Confirmed conflicts are considered before the score. Missing information is not
treated as a contradiction.
"""

AUTHORS_GUIDE_MD = """
Agent Ref extracts every surname supplied by the student and compares it with
Crossref, PubMed, PMC, arXiv or NASA ADS metadata.

- Every supplied author must match, including the first author; the supplied
  list must cover at least **80% of the metadata authors**.
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

st.sidebar.caption(f"Validator {VALIDATOR_VERSION} · {VALIDATOR_SHA256[:12]}")

def configured_ads_token():
    try:
        return str(st.secrets.get("ADS_API_TOKEN", "") or os.environ.get("ADS_API_TOKEN", ""))
    except FileNotFoundError:
        return os.environ.get("ADS_API_TOKEN", "")

ads_api_token = configured_ads_token()
st.sidebar.caption("arXiv: enabled · NASA ADS: " + ("configured" if ads_api_token else "token required"))
if st.session_state.get("validator_sha256") != VALIDATOR_SHA256:
    st.session_state["validation_results"] = []
    st.session_state["last_checked_count"] = 0
    st.session_state["validator_sha256"] = VALIDATOR_SHA256

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
          <a href="https://github.com/FoxHin5431/Agent_ref_Beta">Source code</a>
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

physics_examples = Path(__file__).resolve().parent / "examples" / "physics"
if (physics_examples / "arxiv-20.txt").is_file():
    with st.sidebar:
        st.markdown("**Physics Beta test**")
        st.write("20 real arXiv physics references, including DOI, PDF, identifier and older-format examples. "
                 "These checks verify the cited record; suitability and later publication are not assessed.")
        example_text = (physics_examples / "arxiv-20.txt").read_text(encoding="utf-8")
        if st.button("Load 20 arXiv examples", key="load_arxiv_examples"):
            st.session_state["ref_input"] = example_text
            st.session_state["validation_results"] = []
        st.download_button("Download 20 references", example_text, "arxiv-20.txt", mime="text/plain")
        altered_text = (physics_examples / "arxiv-4-altered.txt").read_text(encoding="utf-8")
        st.download_button("Download 4 deliberately altered references", altered_text,
                           "arxiv-4-altered.txt", mime="text/plain")
        st.caption("The altered pack changes one author, one title, one year and one identifier. "
                   "None should be marked Real. First-time arXiv checks may take about a minute.")

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
        "Metadata Source", "Metadata Record URL", "Metadata DOI", "Metadata Year", "Metadata Journal", "arXiv ID", "ADS ID",
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
    metadata_doi = _display_text(row.get("Metadata DOI") or row.get("Crossref DOI")).strip()
    if bool(row.get("doi_explicit_ok")):
        doi_status = "Matched"
        doi_explanation = "The DOI supplied in the reference resolves."
    elif bool(row.get("doi_derived_ok")):
        doi_status = "Metadata only"
        doi_explanation = "Agent Ref found this DOI; it did not add points to the score."
    elif submitted_doi:
        doi_status = "Not verified"
        doi_explanation = "The supplied DOI could not be confirmed; this alone does not establish fabrication."
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
    metadata_year = _display_text(row.get("Metadata Year") or row.get("Crossref Year") or row.get("PubMed Year"))
    submitted_journal = _display_text(row.get("Extracted Journal")) or extract_journal(reference)
    metadata_journal = _display_text(row.get("Metadata Journal") or row.get("Crossref Journal") or row.get("PubMed Journal"))

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
    if row.get("arXiv ID") or row.get("ADS ID"):
        rows.insert(0, {"Part": "Identifier", "Submitted reference": row.get("arXiv ID") or row.get("ADS ID"),
            "Trusted record": row.get("Metadata Record URL") or "—", "Status": "Not verified",
            "What this means": "Checked using the cited database identifier."})
    evidence = row.get("Comparison evidence")
    if isinstance(evidence, dict):
        for part in rows:
            field = evidence.get(part["Part"].lower())
            if field:
                part["Status"] = {"matched": "Matched", "conflicting": "Different", "unknown": "Not verified"}[field["status"]]
                part["What this means"] = field["reason"]
    return pd.DataFrame(rows)


def style_part_by_part(df: pd.DataFrame):
    colors = {
        "Matched": "background-color: #D9FBE8; color: #086B4E; font-weight: 700",
        "Not verified": "background-color: #FFF3CD; color: #856404; font-weight: 700",
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
                            ads_token=ads_api_token,
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
    manual_count = int(counts.get(MANUAL_REVIEW_LABEL, 0)) + int(counts.get(UNVERIFIED_LABEL, 0))
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
        ("review", manual_count + web_count, "Review required or web"),
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
        "Select a summary card to filter the references below. "
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
            return "manual review" in label or "web source" in label or "unable to verify" in label
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
        if active_filter == "review":
            visible_part_df = part_df[["Part", "Status"]].copy()
            visible_part_df["Source link"] = ""
            if not visible_part_df.empty:
                visible_part_df.loc[visible_part_df.index[0], "Source link"] = (
                    _display_text(selected.get("Source URL")).strip()
                )
        else:
            visible_part_df = part_df[
                ["Part", "Submitted reference", "Trusted record", "Status"]
            ]

        part_column_config = {
            "Part": st.column_config.TextColumn("Part", width="small"),
            "Submitted reference": st.column_config.TextColumn(
                "Submitted reference", width="large"
            ),
            "Trusted record": st.column_config.TextColumn(
                "Trusted record", width="large"
            ),
            "Status": st.column_config.TextColumn("Status", width="small"),
        }
        if active_filter == "review":
            part_column_config["Source link"] = st.column_config.LinkColumn(
                "Source link",
                display_text="Open link",
                width="small",
            )

        st.dataframe(
            style_part_by_part(visible_part_df),
            use_container_width=True,
            hide_index=True,
            height=390,
            column_config=part_column_config,
        )

    with tabs[1]:
        overview_columns = [
            "Ref #", "Validation Result", "Score", "Reference", "Notes"
        ]
        overview_labels = [
            "Reference",
            "Result",
            "Evidence score",
            "Submitted reference",
            "Explanation",
        ]
        overview = df.iloc[filtered_indices][overview_columns].copy()
        overview.columns = overview_labels
        overview_column_config = {
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
        }
        st.dataframe(
            overview,
            use_container_width=True,
            hide_index=True,
            height=430,
            column_config=overview_column_config,
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
      <a href="https://github.com/FoxHin5431/Agent_ref_Beta">Source code</a>
    </div>
    """,
    unsafe_allow_html=True,
)
