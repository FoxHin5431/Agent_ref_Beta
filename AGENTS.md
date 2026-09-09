# Agent Ref validator maintenance

The canonical validation source is `reference_checker/agent_ref_validator`.
This repository consumes a generated shared validator release. Make validation changes in the canonical `FoxHin5431/reference_checker` repository, then use its `tools/sync_validator.py --write` to update this checkout. Do not patch a private copy of validation logic in this interface.

Preserve app-specific interfaces and logging. Before publishing a shared release, run its integrity check, canonical regression suite, and each interface test suite. Verify the same release manifest across pilot, Beta, Regex Inspector and Agent-Ref-002. Never publish secrets.

Deployment: `https://agent-ref-beta.streamlit.app/` tracks this repository’s `main` branch.
