"""Run each actual Streamlit interface with the same saved bibliographic record."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock, patch

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))
from agent_ref_validator import core
from streamlit.testing.v1 import AppTest

fixture_path = repo / 'agent_ref_validator/tests/test_sciencedirect_urls.py'
sys.path.insert(0, str(fixture_path.parent))
spec = importlib.util.spec_from_file_location('fixture', fixture_path)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

entrypoint = 'inspector.py' if (repo / 'inspector.py').exists() else ('streamlit_app.py' if (repo / 'streamlit_app.py').exists() else 'app.py')
app = AppTest.from_file(str(repo / entrypoint), default_timeout=40)
app.secrets['LOGGING_ENABLED'] = False
app.secrets['REFERENCE_CHECKER_APP_PATH'] = ''
if entrypoint == 'inspector.py':
    import validator_loader
    with patch.object(validator_loader, 'load_validator_core', return_value=core):
        app.run()
else:
    app.run()
if app.exception:
    raise AssertionError([item.message for item in app.exception])
captions = [caption.value for caption in app.caption]
assert any(core.VALIDATOR_VERSION in value for value in captions), captions

response = Mock(status_code=200)
response.json.return_value = {'message': {'items': [fixture.ARTICLE]}}
with patch.object(core, 'fetch_crossref_bib', AsyncMock(return_value=response)), patch.object(core, 'check_doi_org_head', AsyncMock(return_value=False)):
    result = asyncio.run(core.validate_single_ref(None, fixture.REFERENCE + fixture.SUFFIXES[2]))
result['Ref #'] = 1

if entrypoint == 'inspector.py':
    app.text_area(key='ref_input').set_value(fixture.REFERENCE + fixture.SUFFIXES[2])
    doi_response = Mock(status_code=200)
    doi_response.json.return_value = {'message': fixture.ARTICLE}
    with patch.object(core, 'fetch_crossref_bib', AsyncMock(return_value=response)), patch.object(core, 'fetch_crossref_doi', AsyncMock(return_value=doi_response)), patch.object(core, 'check_doi_org_head', AsyncMock(return_value=False)):
        next(button for button in app.button if button.label == 'Extract and validate references').click().run()
elif entrypoint == 'app.py':
    app.session_state['validation_results'] = [result]
    app.session_state['last_checked_count'] = 1
    app.run()
else:
    # 002 keeps results inside its submit block, so exercise the button path.
    reference_input = next(text for text in app.text_area if text.label == 'Paste references here:')
    reference_input.set_value(fixture.REFERENCE + fixture.SUFFIXES[2])
    button = next(button for button in app.button if 'Check References' in button.label)
    with patch.object(core, 'fetch_crossref_bib', AsyncMock(return_value=response)), patch.object(core, 'check_doi_org_head', AsyncMock(return_value=False)):
        button.click().run()
if app.exception:
    raise AssertionError([item.message for item in app.exception])
print(f'{repo.name}: startup and results UI passed; validator {core.VALIDATOR_VERSION}')
