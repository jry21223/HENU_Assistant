import asyncio
from pathlib import Path
from henu_mcp import api
from henu_plugin.hardened_service import HardenedHenuPluginService
from components.cli_tools.henu_cli_safe import HenuCliSafe
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
from campus_core.bot import HenuCampusBot
from test_confirmation_shortcut import Plugin


def test_cli_carries_campus_in_compact_options_and_reply(monkeypatch):
    async def run():
        bot = HenuCampusBot('fixture', '', saved_cookies={'_v4_token': 'fixture'})
        monkeypatch.setattr(bot, '_is_token_valid', lambda: True)
        monkeypatch.setattr(bot, '_post_json', lambda *a, **k: {'code': 0, 'data': {'area': [
            {'id': '18', 'name': '读书室'}, {'id': '67', 'name': '第一借书处'}]}})
        monkeypatch.setattr(api, 'library_query', lambda **kw: bot.list_locations())
        plugin = Plugin();plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        tool = HenuCliSafe();tool.plugin = plugin
        session = Session(sender_id='user-a', launcher_id='user-a', launcher_type=LauncherTypes('person'))
        result = await tool.call({'command': 'library locations'}, session, 1)
        assert result['success'] is True
        assert [item.get('campus') for item in result['location_options']] == ['金明', '明伦']
        assert '金明 · 读书室' in result['reply_text']
        assert '明伦 · 第一借书处' in result['reply_text']
    asyncio.run(run())


def test_current_appointment_reply_shows_only_school_details(monkeypatch):
    async def run():
        record = {'areaName': '701七层南自习室', 'no': '002',
                  'showTime': '2026-10-10 08:00:00～22:59:59',
                  'msg': '<b>请于 2026-10-10 08:45 之前签到</b>',
                  'id': 'private-internal-id', 'status': 2}
        monkeypatch.setattr(api, 'library_query', lambda **kw: {'success': True, 'appointments': [record]})
        plugin = Plugin();plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        tool = HenuCliSafe();tool.plugin = plugin
        session = Session(sender_id='user-a', launcher_id='user-a', launcher_type=LauncherTypes('person'))
        result = await tool.call({'command': 'library current'}, session, 1)
        reply = result['reply_text']
        assert '002号' in reply and '08:45' in reply and '22:59:59' in reply
        assert 'private-internal-id' not in reply and 'status=' not in reply and '**' not in reply
        assert len(reply) < 150
        monkeypatch.setattr(api, 'library_query', lambda **kw: {'success': True, 'appointments': []})
        empty = await tool.call({'command': 'library current'}, session, 2)
        assert empty['reply_text'] == '当前没有有效预约。'
    asyncio.run(run())
