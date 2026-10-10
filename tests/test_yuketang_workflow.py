from henu_plugin.cli import inspect_cli_command
import pytest
import asyncio
from pathlib import Path
from unittest.mock import Mock
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from components.cli_tools.henu_cli import HenuCli
from henu_plugin import bridge_client, yuketang_config
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.storage_adapter import PluginStorageAdapter


class Storage:
    def __init__(self):
        self.data = {}
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])

    async def get_plugin_storage_keys(self):
        return list(self.data)

    async def get_plugin_storage(self, key):
        return self.data.get(key, b'{}')

    async def set_plugin_storage(self, key, value):
        self.data[key] = value


def test_misspelled_option_does_not_partially_apply_exam_configuration():
    spec = inspect_cli_command('yuketang exam set --auto-answer off --slvae on')
    assert spec.error and '不支持' in spec.error
    assert spec.resolved_tool is None
    assert spec.params == {}


@pytest.mark.parametrize('command', [
    'yuketang exam set --auto-answer off --master',
    'yuketang exam set --auto-answer on --auto-answer off',
    'yuketang exam set --auto-answer on --auto_answer on',
    'yuketang exam set --auto-answer on unexpected',
    'yuketang lesson set --auto-answer off --enter-dely 30',
    'yuketang lesson set --auto-enter on --llm=',
    'yuketang domain set --domain www --domian pro',
    'yuketang account set --account 13800000000 --password demo --pasword demo',
    'yuketang login --froce',
    'yuketang login --force maybe',
    'yuketang status --force',
    'yuketang enable extra',
    'yuketang disable --foo',
    'yuketang logout extra',
    'yuketang config show exam extra',
    'yuketang config show --unknown',
    'yuketang exam list --course 数据结构 --course 数据结构',
    'yuketang lesson start-time set --course 数据结构 --slots 1=08:00 --typo on',
    'yuketang lesson start-time clear --course 数据结构 --slots 1=08:00',
    'yuketang exam whitelist add 数据结构 --foo',
    'yuketang codes add abc --foo',
])
def test_malformed_rain_command_is_rejected_as_a_whole(command):
    spec = inspect_cli_command(command)
    assert spec.error
    assert spec.resolved_tool is None and spec.params == {}


def test_status_does_not_claim_running_for_stale_registered_executor(monkeypatch):
    async def run():
        plugin = Storage()
        adapter = PluginStorageAdapter(plugin, 'workflow-user')
        paths = await adapter.load_all()
        config = yuketang_config.default_config()
        config['enabled'] = True
        yuketang_config.save_config(paths.yuketang_config_file, config)
        await adapter.save_all()
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: {'url': 'https://test.invalid'})
        monkeypatch.setattr(bridge_client, 'fetch_status', lambda *args, **kwargs: {
            'ok': True, 'config_present': True, 'config_hash': bridge_client.canonical_hash(config),
            'cookie': {'cookie': 'must-never-output'}, 'daemon_wired': True,
            'daemon': {'age_s': 1000, 'openids': ['workflow-user', 'other-private-user']},
        })
        tool = HenuCli()
        tool.plugin = plugin
        session = provider_session.Session(sender_id='workflow-user', launcher_id='workflow-user',
                                           launcher_type=provider_session.LauncherTypes.PERSON)
        result = await tool.call({'command': 'yuketang status'}, session, 1)
        assert result['bridge']['monitoring_state'] == 'unverified'
        assert result['bridge']['heartbeat_fresh'] is False
        assert '无法确认' in result['reply_text']
        assert 'must-never-output' not in str(result) and 'other-private-user' not in str(result)
    asyncio.run(run())


@pytest.mark.parametrize('enabled,registered,age,wired,matching,expected', [
    (True, True, 15, True, True, 'running'),
    (False, False, 15, True, True, 'stopped'),
    (True, False, 15, True, True, 'unverified'),
    (False, True, 15, True, True, 'unverified'),
    (True, True, 75, True, True, 'running'),
    (True, True, 76, True, True, 'unverified'),
    (True, True, -1, True, True, 'unverified'),
    (True, True, True, True, True, 'unverified'),
    (True, True, None, True, True, 'unverified'),
    (True, None, 15, True, True, 'unverified'),
    (True, True, 15, False, True, 'unverified'),
    (True, True, 15, True, False, 'unverified'),
])
def test_status_verifies_configuration_registration_and_fresh_heartbeat(
    monkeypatch, enabled, registered, age, wired, matching, expected,
):
    async def run():
        plugin = Storage()
        adapter = PluginStorageAdapter(plugin, 'workflow-user')
        paths = await adapter.load_all()
        config = yuketang_config.default_config()
        config['enabled'] = enabled
        yuketang_config.save_config(paths.yuketang_config_file, config)
        await adapter.save_all()
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: {'url': 'https://test.invalid'})
        monkeypatch.setattr(bridge_client, 'push_config', lambda u, cfg: {
            'ok': True, 'hash': bridge_client.canonical_hash(cfg)})
        identities = None if registered is None else (['workflow-user'] if registered else [])
        monkeypatch.setattr(bridge_client, 'fetch_status', lambda *args, **kwargs: {
            'ok': True, 'config_present': True,
            'config_hash': bridge_client.canonical_hash(config) if matching else 'different',
            'daemon_wired': wired, 'daemon': {'age_s': age, 'openids': identities},
        })
        tool = HenuCli()
        tool.plugin = plugin
        session = provider_session.Session(sender_id='workflow-user', launcher_id='workflow-user',
                                           launcher_type=provider_session.LauncherTypes.PERSON)
        result = await tool.call({'command': 'yuketang status'}, session, 1)
        assert result['bridge']['monitoring_state'] == expected
        assert result['bridge']['identity_registered'] is registered
        assert result['bridge']['exam_submission_state'] == 'not_checked'
        assert '监听配置' in result['reply_text']
    asyncio.run(run())


def test_rejected_configuration_does_not_create_confirmation_or_push_bridge(monkeypatch):
    async def run():
        plugin = Storage()
        tool = HenuCli()
        tool.plugin = plugin
        session = provider_session.Session(sender_id='workflow-user', launcher_id='workflow-user',
                                           launcher_type=provider_session.LauncherTypes.PERSON)
        push = Mock()
        monkeypatch.setattr(bridge_client, 'push_config', push)
        result = await tool.call({'command': 'yuketang exam set --auto-answer off --slvae on'}, session, 1)
        assert result['success'] is False
        assert not result.get('confirmation_required') and not push.called
        assert not plugin.data.get('user:workflow-user:pending_operation')
    asyncio.run(run())


@pytest.mark.parametrize('command', [
    'yuketang login --force',
    'yuketang login --force off',
    '雨课堂 考试 设置 --auto_answer on --llm on',
    'yuketang domain set --domain=www',
    'yuketang exam whitelist add "010 班"',
    'yuketang exam set --x-access-token "token containing spaces"',
])
def test_documented_rain_aliases_and_quoted_values_remain_supported(command):
    assert not inspect_cli_command(command).error


@pytest.mark.parametrize('option', ['ppt', 'si', 'progress', 'paper'])
def test_exam_token_cannot_hide_forbidden_configuration(option):
    spec = inspect_cli_command(f'yuketang exam set --x-access-token demo --{option} on')
    assert spec.error and spec.resolved_tool is None and spec.params == {}
