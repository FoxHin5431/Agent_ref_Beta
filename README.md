# Agent Ref (Beta)

## Physics database trial — 12 September 2026

This Beta candidate uses shared validator **2026.09.12.2-beta2**, including the
previous locally tested evidence-review fixes. It adds arXiv lookups (including
arXiv DOIs via their own metadata sources) and NASA ADS. Set `ADS_API_TOKEN` in
Streamlit secrets to enable ADS; the token is sent only to the ADS API and is not
included in results. arXiv needs no token. A sidebar indicator reports whether
ADS is configured; failed lookups remain visible as Unable to verify.

Open **Physics Beta test pack**, choose **Load 20 arXiv examples**, then
**Check references**. The two downloads provide 20 real examples and four
deliberately altered controls. The examples should produce 20 Real results when
the metadata services are available. None of the altered controls should pass.

This release verifies the cited record. It does not check assignment suitability
or search for a subsequent published version. First-time arXiv requests are
spaced at least three seconds apart; successful metadata is cached for 24 hours.

The user requested a staged rollout: publish this candidate to Beta only before
promoting it to the pilot. The pilot, Inspector and 002 remain on their hosted
release. This temporary trial supersedes the simultaneous-release instructions
below. The canonical source remains `reference_checker/agent_ref_validator`.

To verify the candidate locally from the workspace root:

```powershell
& reference_checker/.venv/Scripts/python.exe reference_checker/tools/sync_validator.py --target Agent_ref_Beta
```

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
