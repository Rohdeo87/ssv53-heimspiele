"""Results cache isolation: no Azure, upstream, or hardware access in tests."""
import json
import azure.functions as func
from integrations.results import results_blueprint as results


def request(sport='handball', headers=None, method='GET'):
    return func.HttpRequest(method, 'https://example.test/api/ssv-results',
                           params={'sport': sport}, headers=headers or {}, body=b'')


def test_read_uses_only_cache_and_conditional_etag(monkeypatch):
    payload = {'schemaVersion': 1, 'sport': 'handball', 'teams': [], 'stale': False}
    monkeypatch.setattr(results, '_load', lambda sport: payload)
    monkeypatch.setattr(results, 'Fetcher', lambda *a: (_ for _ in ()).throw(AssertionError('upstream')))
    response = results.ssv53_results_read(request())
    assert response.status_code == 200
    assert json.loads(response.get_body()) == payload
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    cached = results.ssv53_results_read(request(headers={'If-None-Match': response.headers['ETag']}))
    assert cached.status_code == 304


def test_invalid_sport_and_preflight_do_not_read_cache(monkeypatch):
    monkeypatch.setattr(results, '_load', lambda *a: (_ for _ in ()).throw(AssertionError('cache')))
    assert results.ssv53_results_read(request('football')).status_code == 400
    assert results.ssv53_results_read(request(method='OPTIONS')).status_code == 204


def test_missing_cache_is_not_fake_results(monkeypatch):
    monkeypatch.setattr(results, '_load', lambda sport: None)
    response = results.ssv53_results_read(request())
    assert response.status_code == 503
    assert response.headers['Cache-Control'] == 'no-store'


def test_cache_error_does_not_leak_configuration(monkeypatch):
    def failed(sport):
        raise RuntimeError('private-canary-value')
    monkeypatch.setattr(results, '_load', failed)
    response = results.ssv53_results_read(request())
    assert response.status_code == 503
    assert b'private-canary-value' not in response.get_body()


def test_timer_defaults_disabled(monkeypatch):
    monkeypatch.delenv('SSV_RESULTS_ENABLED', raising=False)
    monkeypatch.setattr(results, '_load', lambda *a: (_ for _ in ()).throw(AssertionError('cache')))
    results.ssv53_results_update(None)


def test_cache_failure_prevents_fetch_and_write(monkeypatch):
    monkeypatch.setenv('SSV_RESULTS_ENABLED', 'true')
    calls = []
    def failed(sport):
        calls.append(sport)
        raise RuntimeError('unavailable')
    monkeypatch.setattr(results, '_load', failed)
    monkeypatch.setattr(results, 'Fetcher', lambda *a: calls.append('upstream'))
    monkeypatch.setattr(results, '_save', lambda *a: calls.append('write'))
    results.ssv53_results_update(None)
    assert calls == ['handball', 'volleyball']


def test_retry_window_prevents_upstream_requests(monkeypatch):
    monkeypatch.setenv('SSV_RESULTS_ENABLED', 'true')
    monkeypatch.setattr(results, '_load', lambda sport: {'nextAttemptAt': '2099-01-01T00:00:00Z'})
    calls = []
    monkeypatch.setattr(results, 'Fetcher', lambda *a: calls.append('upstream'))
    results.ssv53_results_update(None)
    assert not calls
