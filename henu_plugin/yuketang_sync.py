"""Synchronize only committed Storage state; never send a staged candidate."""
from __future__ import annotations

import asyncio

from henu_plugin import bridge_client, yuketang_config
from henu_plugin.storage_adapter import PluginStorageAdapter


async def sync_after_commit(plugin, storage_key: str, result) -> None:
    if not isinstance(result, dict) or not result.pop('_yuketang_sync_required', False):
        return
    openid = result.pop('_yuketang_openid', storage_key)
    adapter = PluginStorageAdapter(plugin, storage_key)
    status = 'pending'
    try:
        # Re-load the committed snapshot under the per-user lock. Concurrent
        # updates therefore push the latest committed config, never an old one.
        paths = await adapter.load_all()
        config = yuketang_config.load_config(paths.yuketang_config_file)
        if bridge_client.bridge_settings() is not None:
            remote = await asyncio.to_thread(bridge_client.push_config, openid, config)
            if remote.get('ok') is True and remote.get('hash') == bridge_client.canonical_hash(config):
                status = 'synced'
    except Exception:
        # Persistence succeeded; a bridge failure is not a failed local write.
        status = 'pending'
    finally:
        await adapter.abort()
    previous = result.get('bridge') if isinstance(result.get('bridge'), dict) else {}
    result['bridge'] = {**previous, 'status': status}
    note = ('本地已保存，桥端已接收最新配置；实际运行状态请查询 yuketang status。'
            if status == 'synced' else
            '本地已保存，桥端待同步；下次 yuketang status 将重试同步。'
            '若本次要求关闭功能，尚不能确认远端已停止。')
    result['reply_text'] = str(result.get('reply_text') or result.get('msg') or '') + '\n' + note
