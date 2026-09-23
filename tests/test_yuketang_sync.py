import asyncio
import json
from unittest.mock import Mock

from henu_plugin import bridge_client, yuketang_config
from henu_plugin.storage_adapter import PluginStorageAdapter
from henu_plugin.yuketang_sync import sync_after_commit


class Storage:
    def __init__(self):
        self.data = {}

    async def get_plugin_storage(self, key):
        return self.data.get(key, b'{}')

    async def set_plugin_storage(self, key, value):
        self.data[key] = value


def test_sync_uses_committed_config_and_reports_pending(monkeypatch):
    async def run():
        plugin = Storage()
        adapter = PluginStorageAdapter(plugin, 'test-user')
        paths = await adapter.load_all()
        config = yuketang_config.default_config()
        config['enabled'] = True
        yuketang_config.save_config(paths.yuketang_config_file, config)
        post = Mock(side_effect=bridge_client.BridgeError('offline'))
        monkeypatch.setattr(bridge_client, 'push_config', post)
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: {'url': 'https://test.invalid'})
        assert not post.called
        await adapter.save_all()
        result = {'success': True, '_yuketang_sync_required': True}
        await sync_after_commit(plugin, 'test-user', result)
        assert post.call_args.args[1]['enabled'] is True
        assert result['bridge']['status'] == 'pending'
        assert '尚不能确认远端已停止' in result['reply_text']
        assert '_yuketang_sync_required' not in result
    asyncio.run(run())
