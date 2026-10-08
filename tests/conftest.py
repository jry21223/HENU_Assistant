"""Synthetic signed Core HTTP service, without intercepting supplier requests."""
import datetime as dt
import json

import pytest

BENEFITS = ['yuketang', 'ucampus', 'chaoxing', 'library_schedule']

@pytest.fixture
def core(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('')
    monkeypatch.setattr('henu_plugin.kit_client.ENV_FILE', path)
    values = {'CORE_URL':'https://core.test.invalid', 'PORTAL_URL':'https://portal.test.invalid',
              'CLIENT_ID':'test-bot','KEY_ID':'test-key','SECRET':'s'*32,'BOT_UUID':'bot'}
    for key, value in values.items():
        monkeypatch.setenv('HENU_KIT_'+key, value)
    original_post = __import__('requests').post
    data = {'bound':True,'plan':'lifetime','lifetime':True,'allowed':True,'benefits':BENEFITS,
            'checked_at':dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')}
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def iter_content(self,_): yield json.dumps({'data':data}).encode()
    def post(url, **kwargs):
        if url != 'https://core.test.invalid/api/v1/qq-bindings/benefits':
            return original_post(url, **kwargs)
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr('requests.post', post)
    return data,calls,Response
