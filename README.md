# Agent Ref (Beta)

Streamlit app for validating reference lists using DOI (Crossref + doi.org), PubMed/PMC, and heuristic checks.

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
