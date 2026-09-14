# Agent Ref (Beta)

**Agent Ref is a reference-verification tool designed for student assessment and academic-integrity review.**

Agent Ref grew out of a practical problem in higher education: references in student work may be inaccurate, incomplete or potentially fabricated, but failure to find a reference automatically should not itself be treated as evidence of fabrication.

The tool accepts raw reference lists, extracts individual citations and checks them against trusted bibliographic and web sources. It separates references that can be verified from those that contain discrepancies or require human review.

🔗 **Try the current beta:**  
https://agent-ref-beta.streamlit.app/

> Agent Ref is an independent beta project developed by Mark Hintze, Lecturer in Biology at The Open University. It is not an official Open University service.

## Why Agent Ref?

Most reference-verification tools are designed around scholarly papers and relatively well-structured bibliographies.

Agent Ref has been developed around the less tidy references encountered in student assessment, including:

- Harvard-style reference lists
- journal articles with and without DOIs
- PubMed and PMC records
- preprints
- websites and organisational sources
- databases and datasets
- incomplete or inconsistent citations
- references containing incorrect bibliographic information

The aim is **not** to determine whether a student has committed academic misconduct. Agent Ref is a decision-support tool that identifies references that can be verified and highlights those that warrant further human investigation.

## Current verification

Agent Ref currently uses evidence from sources including:

- Crossref
- DOI.org
- PubMed / PMC
- arXiv
- NASA ADS
- publisher and web-source metadata where appropriate

References are compared using identifiers and bibliographic information including title, authors, year and source metadata.

Author validation compares every explicitly supplied surname with trusted metadata. Complete author lists use bidirectional coverage, while `et al.` references check each author named before `et al.`. Confirmed co-author conflicts are shown in the result diagnostics and prevent an explicit DOI reference being marked Real automatically.

Links carrying `utm_source=chatgpt.com` are marked in the **AI URL flag** and **AL notes** fields. This is an advisory provenance signal and does not change the validation result or score.

## Pilot and testing

Agent Ref is currently being piloted in assessment-related work at The Open University, with data being collected on verification accuracy, false positives and the types of references that require manual review.

A separate development benchmark of approximately 180 references is used to test the validator against genuine references, deliberately altered metadata and difficult real-world citation formats.

In a recent small test, Agent Ref extracted and processed **25 raw Harvard-style references in around 7 seconds**.

The project remains in beta and results should always be interpreted by a human reviewer.

## Current limitations and roadmap

Current development includes:

- support for additional citation styles, including APA, Vancouver and Nature
- greater tolerance of malformed citation metadata
- improved classification of non-journal sources
- continued testing against real-world assessment references
- expansion of subject-specific metadata sources

Agent Ref does not attempt to determine academic misconduct automatically.

---

## Physics database trial — 12 September 2026

This Beta candidate uses shared validator **2026.09.12.2-beta2**, including the previous locally tested evidence-review fixes.

It adds arXiv lookups, including arXiv DOIs via their own metadata sources, and NASA ADS.

Set `ADS_API_TOKEN` in Streamlit secrets to enable ADS. The token is sent only to the ADS API and is not included in results. arXiv needs no token.

A sidebar indicator reports whether ADS is configured; failed lookups remain visible as **Unable to verify**.

Open **Physics Beta test pack**, choose **Load 20 arXiv examples**, then **Check references**.

The two downloads provide 20 real examples and four deliberately altered controls. The examples should produce 20 Real results when the metadata services are available. None of the altered controls should pass.

This release verifies the cited record. It does not check assignment suitability or search for a subsequent published version.

First-time arXiv requests are spaced at least three seconds apart; successful metadata is cached for 24 hours.

This candidate is currently being trialled in Beta before promotion to the pilot. The pilot, Inspector and 002 remain on their hosted release. The canonical source remains `reference_checker/agent_ref_validator`.

To verify the candidate locally from the workspace root:

```powershell
& reference_checker/.venv/Scripts/python.exe reference_checker/tools/sync_validator.py --target Agent_ref_Beta
```

## Reference debugging

The interface includes a **Reference debugging tool** link to the hosted Regex Inspector for examining extracted fields and validation comparisons.

## Run locally

1. Create a virtual environment and install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create a secrets file:

   - Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   - Set `LOGGING_ENABLED` and, optionally, `DB_URL`

3. Run:

   ```bash
   streamlit run app.py
   ```

## Run tests

```bash
python -m unittest discover -s tests -v
```

The regression suite uses saved metadata rather than live API calls, so lookup availability does not affect the result.

## Logging (optional)

Logging is controlled by Streamlit secrets:

```toml
LOGGING_ENABLED = true
DB_URL = "postgresql://..."
```

If secrets are not configured, logging is disabled automatically.

## Notes

- Do not commit `.streamlit/secrets.toml` — it is ignored.
- CSV/XLSX exports are generated at runtime.

## Shared validator release

All online validation uses `agent_ref_validator/core.py`.

The canonical source is:

https://github.com/FoxHin5431/reference_checker/tree/main/agent_ref_validator

Beta, Regex Inspector and Agent-Ref-002 carry generated, byte-identical releases of that package so private-repository credentials are not needed at runtime.

Do not edit validation logic in the consumer repositories or restore an old standalone checker. Interfaces and pilot database logging remain app-specific.

Each result includes `Validator Version` and `Validator SHA256`; the interface also displays the release identity.

The release includes the ScienceDirect PII fix and the pilot's Unicode author handling. Adding an Available-at ScienceDirect URL no longer substitutes a guessed DOI for the normal bibliographic lookup.

Run the release and interface checks:

```bash
python -m agent_ref_validator.verify
python -m unittest discover -s agent_ref_validator/tests -v
python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on pushes and pull requests.

`release.json` records hashes of the runtime and regression files so accidental edits fail CI.

Future releases are authored in the canonical repository: bump `VALIDATOR_VERSION`, then run:

```bash
python tools/sync_validator.py --write
```

from that checkout with all three consumer checkouts present.

The command refuses local consumer drift, tests the canonical validator, copies the release and verifies parity.

Run it without `--write` for read-only cross-repository parity checking.

Use repeated `--target PATH` arguments when checkouts are stored elsewhere.

Commit and publish all four repositories together after their checks pass. Streamlit apps tracking `main` redeploy from those commits.

## Deployment

Verified deployment mapping:

https://agent-ref-beta.streamlit.app/ → `main` / `app.py`

## About

Agent Ref is a beta reference-verification tool developed for research and evaluation in academic-integrity workflows.

It has been developed using AI-assisted software development alongside domain-led testing and validation.
