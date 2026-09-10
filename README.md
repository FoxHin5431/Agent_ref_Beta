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

## Shared validator release

All online validation uses `agent_ref_validator/core.py`, release **2026.09.10.1**.
The canonical source is [reference_checker](https://github.com/FoxHin5431/reference_checker/tree/main/agent_ref_validator).
Beta, Regex Inspector and Agent-Ref-002 carry generated, byte-identical releases
of that package so private-repository credentials are not needed at runtime.
Do not edit validation logic in the consumer repositories or restore an old
standalone checker. Interfaces and pilot database logging remain app-specific.

Each result includes `Validator Version` and `Validator SHA256`; the interface
also displays the release identity. The release includes the ScienceDirect PII
fix and the pilot's Unicode author handling. Adding an Available-at ScienceDirect
URL no longer substitutes a guessed DOI for the normal bibliographic lookup.

Run the release and interface checks:

```powershell
python -m agent_ref_validator.verify
python -m unittest discover -s agent_ref_validator/tests -v
python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on pushes and pull requests. `release.json`
records hashes of the runtime and regression files so accidental edits fail CI.
Future releases are authored in the canonical repository: bump
`VALIDATOR_VERSION`, then run `python tools/sync_validator.py --write` from that
checkout with all three consumer checkouts present. The command refuses local
consumer drift, tests the canonical validator, copies the release, and verifies
parity. Run it without `--write` for read-only cross-repository parity checking.
Use repeated `--target PATH` arguments when checkouts are stored elsewhere.
Commit and publish all four repositories together after their checks pass.
Streamlit apps tracking `main` redeploy from those commits.

Verified deployment mapping: [agent-ref-beta.streamlit.app](https://agent-ref-beta.streamlit.app/) → `main` / `app.py`.
