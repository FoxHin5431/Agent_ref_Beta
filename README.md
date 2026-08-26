# Agent Ref (Beta)

Streamlit app for validating reference lists using DOI (Crossref + doi.org), PubMed/PMC, and heuristic checks.

The interface includes a **Reference debugging tool** link to the hosted Regex
Inspector for examining extracted fields and validation comparisons.

Links carrying `utm_source=chatgpt.com` are marked in the **AI URL flag** and
**AL notes** fields. This is an advisory provenance signal and does not change
the validation result or score.

Author validation compares every explicitly supplied surname with trusted
metadata. Complete lists use bidirectional coverage, while `et al.` references
check each author named before `et al.`. Confirmed co-author conflicts are shown
in the result diagnostics and prevent an explicit DOI reference being marked
Real automatically.

## Run locally

1. Create a virtual environment and install dependencies:
   - `pip install -r requirements.txt`

2. Create secrets file:
   - Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   - Set `LOGGING_ENABLED` and (optionally) `DB_URL`

3. Run:
   - `streamlit run app.py`

## Run tests

- `python -m unittest discover -s tests -v`

The regression suite uses saved metadata rather than live API calls, so lookup
availability does not affect the result.

## Logging (optional)

Logging is controlled by Streamlit secrets:

- `LOGGING_ENABLED = true|false`
- `DB_URL = "postgresql://..."`

If secrets are not configured, logging is disabled automatically.

## Notes

- Do not commit `.streamlit/secrets.toml` (it is ignored).
- CSV/XLSX exports are generated at runtime.
