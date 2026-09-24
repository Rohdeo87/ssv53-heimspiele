"""Integration safeguards; no imports/calls to hardware or live Azure."""
import ast
import pytest
from prepare_integration import patch_function, configured_template, prepare, HERE

SOURCE = 'import azure.functions as func\napp = func.FunctionApp()\n@app.route(route="existing")\ndef existing(req):\n    return "unchanged"\n'

def test_registration_additive_and_idempotent():
    result = patch_function(SOURCE)
    assert result.count('app.register_functions(ssv_results_blueprint)') == 1
    assert patch_function(result) == result
    assert 'def existing(req):\n    return "unchanged"' in result
    ast.parse(result)

@pytest.mark.parametrize('source', ['nothing = 1', SOURCE + '\napp = func.FunctionApp()\n', SOURCE.replace('app =','other =')])
def test_ambiguous_entrypoint_refused(source):
    with pytest.raises(ValueError): patch_function(source)

@pytest.mark.parametrize('url', ['http://example.org/api/ssv-results','https://user:pass@example.org/api/ssv-results','https://example.org/api/ssv-results?code=secret','https://example.org/api/other'])
def test_endpoint_secrets_and_wrong_route_refused(url):
    with pytest.raises(ValueError): configured_template('const API_URL = "";', url)

def test_template_configuration_keeps_appack_placeholders():
    source = '${userTitle}\n${appId}\nconst API_URL = "";'
    result = configured_template(source, 'https://example.org/api/ssv-results')
    assert '${userTitle}' in result and '${appId}' in result
    assert 'https://example.org/api/ssv-results' in result

def test_overlay_does_not_change_original_files(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo/'function_app.py').write_text(SOURCE, 'utf-8')
    (repo/'requirements.txt').write_text((HERE/'requirements.txt').read_text('utf-8'), 'utf-8')
    output = tmp_path / 'review'
    manifest = prepare(repo, output)
    assert not manifest['deployed'] and not manifest['existing_source_modified']
    assert (repo/'function_app.py').read_text('utf-8') == SOURCE
    assert not (repo/'results_blueprint.py').exists()
    assert (output/'source-overlay/integrations/results/ssv_results/parsers.py').is_file()
    assert 'register_functions' in (output/'function_app.patch').read_text('utf-8')
    with pytest.raises(ValueError): prepare(repo, output)

def test_changed_dependencies_are_not_silently_overwritten(tmp_path):
    (tmp_path/'function_app.py').write_text(SOURCE, 'utf-8')
    (tmp_path/'requirements.txt').write_text('requests==0.0.0\n', 'utf-8')
    with pytest.raises(ValueError): prepare(tmp_path, tmp_path/'review')
    assert not (tmp_path/'review').exists()
