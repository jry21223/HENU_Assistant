from __future__ import annotations

import io
import json
import sys

import pytest

import henu_cli


def test_stdin_password_keeps_spaces_and_reads_only_one_line(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(sys, 'argv', [
        'henu_cli.py', 'setup_account', '--student_id', 'offline-student',
        '--password-stdin', '--no_verify_login',
    ])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(' synthetic $pass ! \' " \\ \r\nnext-line\n'))
    monkeypatch.setattr(henu_cli, 'setup_account', lambda **kw: saved.append(kw) or {'success': True})

    henu_cli.main()

    output = capsys.readouterr()
    assert saved[0]['password'] == ' synthetic $pass ! \' " \\ '
    assert sys.stdin.readline() == 'next-line\n'
    assert saved[0]['verify_login'] is False
    assert json.loads(output.out)['success'] is True
    assert 'synthetic $pass' not in output.out + output.err


def test_interactive_password_uses_secure_prompt(monkeypatch, capsys):
    import getpass

    monkeypatch.setattr(sys, 'argv', ['henu_cli.py', 'setup_account', '--student_id', 'offline'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(getpass, 'getpass', lambda _prompt: ' synthetic-interactive-secret ')
    saved = []
    monkeypatch.setattr(henu_cli, 'setup_account', lambda **kw: saved.append(kw) or {'success': True})
    henu_cli.main()
    assert saved[0]['password'] == ' synthetic-interactive-secret '
    output = capsys.readouterr()
    assert 'synthetic-interactive-secret' not in output.out + output.err


def test_password_is_not_repeated_when_account_service_raises(monkeypatch, capsys):
    secret = 'synthetic-password-error-detail'
    monkeypatch.setattr(sys, 'argv', [
        'henu_cli.py', 'setup_account', '--student_id', 'offline', '--password', secret,
    ])

    def fail(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(henu_cli, 'setup_account', fail)
    henu_cli.main()
    output = capsys.readouterr()
    assert json.loads(output.out)['success'] is False
    assert secret not in output.out + output.err


@pytest.mark.parametrize('password_input', ['', '\n', '   \r\n'])
def test_empty_standard_input_does_not_reach_school(monkeypatch, capsys, password_input):
    monkeypatch.setattr(sys, 'argv', [
        'henu_cli.py', 'setup_account', '--student_id', 'offline', '--password_stdin',
    ])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(password_input))
    monkeypatch.setattr(henu_cli, 'setup_account', lambda **kw: pytest.fail('school called'))
    henu_cli.main()
    assert json.loads(capsys.readouterr().out)['success'] is False


def test_unattended_call_cannot_fall_back_to_echoed_prompt(monkeypatch, capsys):
    import getpass

    monkeypatch.setattr(sys, 'argv', ['henu_cli.py', 'setup_account', '--student_id', 'offline'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('must-not-be-read\n'))
    monkeypatch.setattr(getpass, 'getpass', lambda _prompt: pytest.fail('insecure prompt'))
    henu_cli.main()
    assert json.loads(capsys.readouterr().out)['success'] is False
    assert sys.stdin.readline() == 'must-not-be-read\n'


def test_conflicting_password_modes_are_rejected(monkeypatch):
    monkeypatch.setattr(sys, 'argv', [
        'henu_cli.py', 'setup_account', '--student_id', 'offline',
        '--password', 'synthetic-secret', '--password_stdin',
    ])
    with pytest.raises(SystemExit) as error:
        henu_cli.main()
    assert error.value.code == 2
