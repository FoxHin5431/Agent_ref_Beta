# Physics Beta test pack

`arxiv-20.txt` contains 20 real references generated from arXiv metadata retrieved
on 12 September 2026. Paste the complete file into Beta, or use **Physics Beta
test pack → Load 20 arXiv examples → Check references**. The expected result is
20 Real records when their metadata services are available. A service outage
should produce Unable to verify, not a metadata mismatch.

The references cover astrophysics, particle physics, gravitation, quantum
physics and condensed matter; first-author and first-three-author `et al.`
forms; collective authors; modern and legacy identifiers; PDF and abstract URLs;
arXiv DOIs; and explicit v1 records. Each original record and retrieval URL is
preserved in `arxiv-20-metadata.json`. These generated examples test routing,
parsing and matching; they are not an independent accuracy benchmark.

`arxiv-4-altered.txt` is a separate negative-control pack. Its four entries change
the first reference's author, title, year and identifier respectively. Expected:
the first three are Suspicious; the nonexistent identifier is Unable to verify.
None should be Real. These deliberately incorrect references are test data.

The check establishes whether a citation matches a source record. It does not
assess whether a preprint is suitable for an assignment or look for subsequent
journal publication. That feature is deferred.

To regenerate from official arXiv metadata:

```powershell
& .venv/Scripts/python.exe tools/prepare_physics_examples.py
& .venv/Scripts/python.exe tools/check_physics_examples.py
& .venv/Scripts/python.exe tools/check_physics_examples.py --live
```
