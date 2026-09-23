import json
from unittest.mock import Mock

import pytest

from henu_plugin import bridge_client as bridge


def test_bridge_reads_plugin_env_in_empty_child_environment(tmp_path, monkeypatch):
    config = tmp_path / '.env'
    config.write_text('HENU_BRIDGE_URL=https://bridge.example.test\nHENU_BRIDGE_SECRET=test-only-secret\n')
    monkeypatch.delenv('HENU_BRIDGE_URL', raising=False)
    monkeypatch.delenv('HENU_BRIDGE_SECRET', raising=False)
    monkeypatch.setattr(bridge, 'ENV_FILE', config, raising=False)
    assert bridge.bridge_settings() == {'url': 'https://bridge.example.test', 'secret': 'test-only-secret'}


def test_bridge_requires_https_and_does_not_follow_redirects(monkeypatch):
    monkeypatch.setenv('HENU_BRIDGE_URL', 'http://bridge.example.test')
    monkeypatch.setenv('HENU_BRIDGE_SECRET', 'test-only-secret')
    with pytest.raises(bridge.BridgeError):
        bridge.fetch_status('test-user')
    monkeypatch.setenv('HENU_BRIDGE_URL', 'https://bridge.example.test')
    post = Mock(return_value=Mock(status_code=302))
    monkeypatch.setattr(bridge.requests, 'post', post)
    with pytest.raises(bridge.BridgeError):
        bridge.fetch_status('test-user')
    assert post.call_args.kwargs['allow_redirects'] is False


def test_bridge_rejects_duplicate_authenticated_response(monkeypatch):
    monkeypatch.setenv('HENU_BRIDGE_URL', 'https://bridge.example.test')
    monkeypatch.setenv('HENU_BRIDGE_SECRET', 'test-only-secret')
    body = bridge._seal('test-only-secret', '/v1/status', {'ok': True})
    monkeypatch.setattr(bridge.requests, 'post', Mock(return_value=Mock(status_code=200, content=body)))
    assert bridge.fetch_status('test-user')['ok'] is True
    with pytest.raises(bridge.BridgeError):
        bridge.fetch_status('test-user')
