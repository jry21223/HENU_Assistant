import httpx
from henu_plugin.qq_navigation_api import NavigationAPI


def test_apply_preserves_other_panels_and_is_idempotent():
    panels = [{'panel_id': 'other', 'panel': {'remark': 'other-plugin', 'items': []}, 'target_type': 'all'}]
    menu = {}
    mutations = []
    def handle(request):
        import json
        nonlocal menu
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.method == 'GET':
            if path == '/v2/menu':
                return httpx.Response(200, json={'menu': menu})
            if path == '/v2/panels':
                return httpx.Response(200, json={'records': panels if request.url.params['scope'] == 'c2c' else [], 'is_end': True})
        mutations.append((request.method, path))
        if path == '/v2/menu':
            menu = body['menu']
            return httpx.Response(200, json={})
        if request.method == 'POST':
            record = {'panel_id': 'new', **body}
            panels.append(record)
            return httpx.Response(200, json={'panel_id': 'new'})
        raise AssertionError(path)
    api = NavigationAPI(httpx.Client(transport=httpx.MockTransport(handle), base_url='https://api.bot.qq.com'))
    desired = {'menu': {'items': []}, 'panels': {'c2c': {'scope': 'c2c', 'target_type': 'all', 'panel': {'remark': 'henu-navigation-v1-c2c', 'items': []}}}}
    snapshot = api.snapshot()
    api.apply(desired, snapshot)
    first_count = len(mutations)
    api.apply(desired, api.snapshot())
    assert len(mutations) == first_count
    assert panels[0]['panel_id'] == 'other'
    assert all(method != 'DELETE' for method, _ in mutations)


def test_http_200_business_error_is_not_treated_as_success():
    import pytest
    api = NavigationAPI(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'err_code': 40030020})), base_url='https://api.bot.qq.com'))
    with pytest.raises(RuntimeError, match='40030020'):
        api.snapshot()


def test_conflicting_managed_panel_is_rejected_before_any_write():
    import pytest
    writes = []
    def handle(request):
        if request.method != 'GET':
            writes.append(request.method)
        if request.url.path == '/v2/menu':
            return httpx.Response(200, json={'menu': {}})
        record = {'panel_id': 'private', 'target_type': 'specific', 'panel': {'remark': 'henu-navigation-v1-c2c', 'items': []}}
        return httpx.Response(200, json={'records': [record] if request.url.params['scope'] == 'c2c' else [], 'is_end': True})
    api = NavigationAPI(httpx.Client(transport=httpx.MockTransport(handle), base_url='https://api.bot.qq.com'))
    desired = {'menu': {'items': [{'name': '帮助', 'type': 'send_message', 'send_message': '使用帮助'}]}, 'panels': {'c2c': {'panel': {'remark': 'henu-navigation-v1-c2c'}}}}
    with pytest.raises(RuntimeError, match='Ambiguous'):
        api.apply(desired, api.snapshot())
    assert writes == []


def test_provider_icons_and_omitted_false_do_not_cause_rewrites():
    writes = []
    menu = {'items': [{'name': '帮助', 'type': 'send_message', 'send_message': '使用帮助'}]}
    panel = {'scope': 'c2c', 'target_type': 'all', 'panel': {'remark': 'henu-navigation-v1-c2c', 'items': [{'name': '使用帮助', 'type': 'command', 'only_admin': False}]}}
    desired = {'menu': menu, 'panels': {'c2c': panel}}
    def handle(request):
        if request.method != 'GET':
            writes.append(request.method)
        if request.url.path == '/v2/menu':
            return httpx.Response(200, json={'menu': {'items': [{**menu['items'][0], 'icon': 'https://qq.test/default.png'}]}})
        record = {'panel_id': 'existing', 'scope': 'c2c', 'target_type': 'all', 'panel': {'remark': panel['panel']['remark'], 'items': [{'name': '使用帮助', 'type': 'command'}]}}
        return httpx.Response(200, json={'records': [record] if request.url.params['scope'] == 'c2c' else [], 'is_end': True})
    api = NavigationAPI(httpx.Client(transport=httpx.MockTransport(handle), base_url='https://api.bot.qq.com'))
    assert api.verify(desired)
    assert api.apply(desired, api.snapshot()) == []
    assert writes == []
