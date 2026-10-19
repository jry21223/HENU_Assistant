import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain, Image

spec = importlib.util.spec_from_file_location('qq_active_overlay', Path(__file__).parents[1] / 'ops/qq_active_transport.py')
overlay = importlib.util.module_from_spec(spec);spec.loader.exec_module(overlay)


def test_active_private_delivery_uses_real_send_and_requires_receipt():
    async def run():
        adapter = SimpleNamespace(_send_c2c_or_group_text_reply=AsyncMock(return_value={'id': 'receipt'}))
        result = await overlay.send_message(adapter, 'person', 'alice', MessageChain(root=[Plain(text='测试提醒')]))
        adapter._send_c2c_or_group_text_reply.assert_awaited_once_with('c2c', 'alice', '测试提醒')
        assert result['id'] == 'receipt'
        adapter._send_c2c_or_group_text_reply.return_value = None
        with pytest.raises(RuntimeError):
            await overlay.send_message(adapter, 'person', 'alice', MessageChain(root=[Plain(text='测试提醒')]))
    asyncio.run(run())


def test_active_delivery_rejects_attachment_or_unknown_target():
    async def run():
        adapter = SimpleNamespace(_send_c2c_or_group_text_reply=AsyncMock())
        with pytest.raises(ValueError):
            await overlay.send_message(adapter, 'person', 'alice', MessageChain(root=[Image(url='https://synthetic.invalid/a.png')]))
        with pytest.raises(ValueError):
            await overlay.send_message(adapter, 'unknown', 'alice', MessageChain(root=[Plain(text='测试')]))
        adapter._send_c2c_or_group_text_reply.assert_not_called()
    asyncio.run(run())
