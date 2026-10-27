"""Campus/building selection and QQ presentation regressions."""
from henu_plugin.cli import inspect_cli_command


def test_building_list_keeps_explicit_campus_text():
    spec = inspect_cli_command('empty classroom buildings --campus-text 金明')
    assert spec.params.get('campus_text') == '金明'


def test_numbered_building_resolves_within_campus(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from henu_mcp.tools import server_impl as api
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    result = api._resolve_campus_and_building('金明', '综合楼1')
    assert result.get('campus_code') == '02', result
    assert result.get('building_code') == '0006', result


def test_jinming_prompt_lists_its_actual_buildings_and_selection_codes(tmp_path, monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from campus_core import storage_paths
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin import self_study as study
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    monkeypatch.setattr(study, '_may_have_current_cache', lambda *args: False)
    call = AsyncMock(return_value={'success': True, 'rooms': [], 'total': 0})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    key = ('bot', 'person', 'owner', 'owner')
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'),
        _self_study_pending={key: {'campus': '金明', 'expires': time.monotonic()+600}})
    def context():
        return SimpleNamespace(event=SimpleNamespace(launcher_type='person',launcher_id='owner',sender_id='owner'),
            get_bot_uuid=AsyncMock(return_value='bot'),get_query_vars=AsyncMock(return_value={}),
            set_query_var=AsyncMock(),query_id=1,reply=AsyncMock(),prevent_default=lambda: None,prevent_postorder=lambda: None)
    async def run():
        ctx = context()
        assert await study.handle_self_study(plugin, ctx, '第2大节')
        text = str(ctx.reply.call_args_list)
        assert '金明综合楼1' in text and '金明综合楼6' in text
        assert '十号楼' not in text and '综合教学楼' not in text
        choices = plugin._self_study_pending[key]['building_choices']
        assert len(choices) == 9 and all(row['campus_code']=='02' for row in choices)
        call.assert_not_awaited()
        assert await study.handle_self_study(plugin, context(), '1')
        command = call.await_args.args[0]['command']
        assert '--campus-code 02' in command
        assert '--building-code ' + choices[0]['building_code'] in command
    asyncio.run(run())


def test_campus_building_list_cli_to_card_never_expands_to_all_campuses(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from campus_core import storage_paths
    from campus_core.empty_classroom.models import Building
    from henu_mcp.tools import server_impl as api
    from henu_plugin.qq_cards import result_card
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    seen = []
    def buildings(campus):
        seen.append(campus)
        assert campus == '02'
        return [Building('0006', '金明综合楼1', '02'), Building('17', '金明综合楼2', '02')]
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: SimpleNamespace(fetch_buildings=buildings))
    spec = inspect_cli_command('empty classroom buildings --campus-text 金明')
    result = api.empty_classroom_query(**spec.params)
    text = '\n'.join(result_card(result, 'empty_classroom_query').pages())
    assert seen == ['02']
    assert '金明综合楼1' in text and '金明综合楼2' in text
    assert '明伦' not in text and '综合教学楼' not in text


def test_building_number_selection_preserves_automatically_offered_period(tmp_path, monkeypatch):
    import asyncio
    import time
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from zoneinfo import ZoneInfo
    from campus_core import storage_paths
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin import self_study as study
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    monkeypatch.setattr(study, '_may_have_current_cache', lambda *args: False)
    monkeypatch.setattr(study, '_saved_clock_matches', AsyncMock(return_value=True))
    now = [datetime(2026, 10, 20, 16, 39, tzinfo=ZoneInfo('Asia/Shanghai'))]
    monkeypatch.setattr(study, 'campus_now', lambda: now[0])
    call = AsyncMock(return_value={'success': True, 'rooms': [], 'total': 0})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    key = ('bot', 'person', 'owner', 'owner')
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'),
        _self_study_pending={key: {'campus': '金明', 'expires': time.monotonic()+600}})
    def context():
        return SimpleNamespace(event=SimpleNamespace(launcher_type='person',launcher_id='owner',sender_id='owner'),
            get_bot_uuid=AsyncMock(return_value='bot'),get_query_vars=AsyncMock(return_value={}),
            set_query_var=AsyncMock(),query_id=1,reply=AsyncMock(),prevent_default=lambda: None,prevent_postorder=lambda: None)
    async def run():
        assert await study.handle_self_study(plugin, context(), '金明')
        assert str(plugin._self_study_pending[key]['period']) == '3'
        now[0] = now[0].replace(hour=16, minute=41)
        assert await study.handle_self_study(plugin, context(), '1')
        call.assert_awaited_once()
        assert '--period 3' in call.await_args.args[0]['command']
    asyncio.run(run())
