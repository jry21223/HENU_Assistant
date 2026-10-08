from henu_plugin.cli import build_help_payload, inspect_cli_command, redact_cli_command


def test_qingmu_commands_follow_space_cli_and_preserve_private_binding():
    spec = inspect_cli_command('青木 courses --platform ucampus_ai')
    assert not spec.error
    assert spec.resolved_tool == 'qingmu_courses'
    assert spec.params == {'platform': 'ucampus_ai'}
    bind = inspect_cli_command("qingmu account set --platform chaoxing --account test-student --password 'Demo secret!' --school 河南大学")
    assert not bind.error
    assert bind.resolved_tool == 'qingmu_account_set'
    assert 'Demo secret!' not in redact_cli_command(bind.raw)
    assert inspect_cli_command('qingmu order add --platform ucampus --course 2').resolved_tool == 'qingmu_order_add'
    assert inspect_cli_command('qingmu order retry --id local-ref').resolved_tool == 'qingmu_order_retry'
    assert inspect_cli_command('qingmu account set --platform ucampus --account a --password b').error
    assert inspect_cli_command('qingmu courses --platform invented').error
    assert inspect_cli_command('qingmu order add --platform ucampus --course 0').error
    assert inspect_cli_command('qingmu courses --platform ucampus --username another').error
    assert inspect_cli_command('qingmu order password --id local-ref').error
    assert 'qingmu status' in build_help_payload('')['commands']
    assert 'qingmu platforms' in build_help_payload('青木')['commands']
