"""Operator-side QQ navigation API; callers persist a snapshot before applying."""
from __future__ import annotations


class NavigationAPI:
    def __init__(self, client):
        self.client = client

    def request(self, method: str, path: str, body=None) -> dict:
        response = self.client.request(method, path, json=body)
        response.raise_for_status()
        result = response.json() if response.content else {}
        code = result.get('err_code') or result.get('code')
        if code:
            # Never include provider bodies, which may echo private data.
            raise RuntimeError(f'QQ navigation error {code}')
        return result

    def snapshot(self) -> dict:
        result = {'menu': self.request('GET', '/v2/menu').get('menu') or {}, 'panels': {}}
        for scope in ('c2c', 'group'):
            records, cursor, seen = [], '', set()
            while True:
                from urllib.parse import urlencode
                page = self.request('GET', '/v2/panels?' + urlencode({'scope': scope, 'limit': 50, 'cursor': cursor}))
                records.extend(page.get('records') or [])
                if page.get('is_end') is True:
                    break
                cursor = page.get('next_cursor') or ''
                if not cursor or cursor in seen:
                    raise RuntimeError('Incomplete QQ panel pagination')
                seen.add(cursor)
            result['panels'][scope] = records
        return result

    @staticmethod
    def panel_content(panel: dict) -> dict:
        return {key: panel.get(key, [] if key == 'items' else '') for key in ('items', 'remark')}

    def apply(self, desired: dict, before: dict) -> list[dict]:
        # Fresh read prevents overwriting changes since the backup was captured.
        if self.snapshot() != before:
            raise RuntimeError('QQ navigation changed after backup; inspect again')
        changes = []
        if before['menu'] != desired['menu']:
            self.request('PUT', '/v2/menu', {'menu': desired['menu']})
            changes.append({'kind': 'menu'})
        for scope, target in desired['panels'].items():
            matches = [record for record in before['panels'][scope]
                       if record.get('panel', {}).get('remark') == target['panel']['remark']]
            if len(matches) > 1 or any(record.get('target_type') != 'all' for record in matches):
                raise RuntimeError('Ambiguous managed QQ panel; inspect manually')
            if not matches:
                created = self.request('POST', '/v2/panels', target)
                if not created.get('panel_id'):
                    raise RuntimeError('Panel creation outcome unknown; inspect before retry')
                changes.append({'kind': 'panel_created', 'panel_id': created['panel_id'], 'scope': scope})
            elif self.panel_content(matches[0]['panel']) != self.panel_content(target['panel']):
                from urllib.parse import quote
                panel_id = str(matches[0]['panel_id'])
                self.request('PUT', '/v2/panels/' + quote(panel_id, safe=''), {'panel': target['panel']})
                changes.append({'kind': 'panel_updated', 'panel_id': panel_id, 'scope': scope})
        return changes

    def verify(self, desired: dict) -> bool:
        actual = self.snapshot()
        if actual['menu'] != desired['menu']:
            return False
        for scope, target in desired['panels'].items():
            matches = [record for record in actual['panels'][scope]
                       if record.get('panel', {}).get('remark') == target['panel']['remark']]
            if len(matches) != 1 or matches[0].get('target_type') != 'all':
                return False
            if self.panel_content(matches[0]['panel']) != self.panel_content(target['panel']):
                return False
        return True
