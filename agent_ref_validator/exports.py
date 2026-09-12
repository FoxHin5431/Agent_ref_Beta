"""Shared Excel export for Agent Ref interfaces."""
import io
import pandas as pd
from .core import MANUAL_REVIEW_LABEL, UNVERIFIED_LABEL

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

        main_cols += ["Manual review", "AL notes", "Notes", "Source URL", "Reference", "Validator Version", "Validator SHA256"]
        main = export_df[[c for c in main_cols if c in export_df.columns]].copy()

    else:
        base_order = ["Ref #", "Validation Result", "Score"]

        if include_review:
            base_order += ["Is Review", "Review Source", "Review Notes"]

        base_order += [
            "Manual review", "AI URL flag", "AL notes",
            "Extracted Authors", "Metadata Authors", "Author match method",
            "Author match count", "Submitted author count", "Metadata author count",
            "Reference", "Domain", "Domains", "Source URL", "Validator Version", "Validator SHA256",
            "Extracted DOI", "DOI Source", "Metadata Source", "Metadata Record URL", "Metadata DOI",
            "Metadata Year", "Metadata Journal", "Source Type", "arXiv ID", "ADS ID",
            "Crossref DOI", "Crossref Journal", "Crossref Year",
            "PubMed ID", "PubMed Journal", "PubMed Year", "Notes"
        ]

        debug_order = [
            "doi_explicit_ok", "doi_derived_ok", "year_ok", "author_ok", "journal_ok",
            "Uses et al.", "First author matches", "Author submitted coverage",
            "Author metadata coverage",
            "title_ok", "title_similarity", "title_match_confidence", "title_match_method",
            "strong_identity_match", "vol_ok", "issue_ok", "pages_ok", "metadata_conflict_count",
            "metadata_conflicts", "Comparison evidence", "Lookup issue", "Review required",
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
            MANUAL_REVIEW_LABEL,
            UNVERIFIED_LABEL
        ])
        | export_df["AI URL flag"].fillna(False).astype(bool)
    ].copy()

    if view == "Condensed":
        issues["DOI score"] = issues.apply(compute_doi_score, axis=1)

        issue_cols = ["Ref #", "Validation Result", "Score", "DOI score", "AI URL flag"]
        if include_review:
            issue_cols += ["Is Review"]

        issue_cols += ["Manual review", "AL notes", "Notes", "Source URL", "Reference", "Validator Version", "Validator SHA256"]
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
                    "type": "text", "criteria": "containing", "value": "Unable to verify", "format": fmt_manual
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
