from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from henu_plugin.cli import (  # noqa: E402
    build_help_payload,
    inspect_cli_command,
    redact_cli_command,
    redact_cli_params,
)
from henu_plugin import yuketang_config as ycfg  # noqa: E402
from henu_plugin import yuketang_nl  # noqa: E402


def _spec(command: str):
    return inspect_cli_command(command)


def _error(command: str) -> str:
    spec = _spec(command)
    assert spec.error, f"期望命令报错: {command}"
    return spec.error


def _no_error(command: str):
    spec = _spec(command)
    assert not spec.error, f"命令不应报错: {command} -> {spec.error}"
    return spec


# ---------- 解析器 ----------

def test_yuketang_status_and_toggle() -> None:
    spec = _no_error("yuketang status")
    assert spec.resolved_tool == "yuketang_status"

    spec = _no_error("yuketang enable")
    assert spec.resolved_tool == "yuketang_set_enabled" and spec.params["enabled"] == "on"
    spec = _no_error("yuketang disable")
    assert spec.resolved_tool == "yuketang_set_enabled" and spec.params["enabled"] == "off"

    assert _spec("雨课堂 status").resolved_tool == "yuketang_status"


def test_yuketang_config_show() -> None:
    spec = _no_error("yuketang config show")
    assert spec.resolved_tool == "yuketang_config_show" and spec.params["topic"] == ""
    spec = _no_error("yuketang config show lesson")
    assert spec.params["topic"] == "lesson"
    assert "用法" in _error("yuketang config")


def test_yuketang_domain_set() -> None:
    spec = _no_error("yuketang domain set --domain pro")
    assert spec.resolved_tool == "yuketang_domain_set" and spec.params["domain"] == "pro"
    assert "缺少参数" in _error("yuketang domain set")
    assert "用法" in _error("yuketang domain")


def test_yuketang_lesson_set() -> None:
    spec = _no_error("yuketang lesson set --auto-answer on --llm on --subjective off --enter-delay 30")
    assert spec.resolved_tool == "yuketang_lesson_set"
    assert spec.params == {
        "auto_enter": "",
        "auto_answer": "on",
        "llm": "on",
        "subjective": "off",
        "enter_delay": "30",
    }
    spec = _no_error("yuketang lesson set --auto-enter off")
    assert spec.params["auto_enter"] == "off"
    assert "整体停用" in _error("yuketang lesson set --ppt on")
    assert "整体停用" in _error("yuketang lesson set --progress on")
    assert "整体停用" in _error("yuketang lesson set --si on")
    assert "属于 exam" in _error("yuketang lesson set --master on")
    assert "私聊单独发送" in _error("yuketang lesson set --x-access-token abc")
    assert "没有可配置项" in _error("yuketang lesson set --foo 1")


def test_yuketang_list_ops() -> None:
    spec = _no_error("yuketang lesson whitelist add 数据结构 操作系统")
    assert spec.resolved_tool == "yuketang_list_update"
    assert spec.params == {"scope": "lesson_whitelist", "op": "add", "items": ["数据结构", "操作系统"]}

    spec = _no_error("yuketang lesson blacklist remove 未央.深度学习")
    assert spec.params["scope"] == "lesson_blacklist" and spec.params["op"] == "remove"

    spec = _no_error("yuketang exam whitelist clear")
    assert spec.params["scope"] == "exam_whitelist" and spec.params["op"] == "clear"

    spec = _no_error("yuketang codes add 94RGB")
    assert spec.params == {"scope": "codes", "op": "add", "items": ["94RGB"]}

    assert "不接受额外" in _error("yuketang lesson whitelist clear 数据结构")
    assert "缺少名称参数" in _error("yuketang lesson whitelist add")
    assert "未知操作" in _error("yuketang codes push X")


def test_yuketang_start_time() -> None:
    spec = _no_error('yuketang lesson start-time set --course 未央.机器学习 --slots "1=08:00,2=13:30"')
    assert spec.resolved_tool == "yuketang_start_time"
    assert spec.params["op"] == "set"
    assert spec.params["course"] == "未央.机器学习"
    assert spec.params["slots"] == "1=08:00,2=13:30"

    spec = _no_error("yuketang lesson start-time clear --course 未央.机器学习")
    assert spec.params["op"] == "clear"

    assert "用法" in _error("yuketang lesson start-time reset --course X")
    assert "缺少参数" in _error("yuketang lesson start-time set --course X")
    assert "缺少参数" in _error("yuketang lesson start-time set --slots 1=08:00")


def test_yuketang_exam_set_and_token() -> None:
    spec = _no_error("yuketang exam set --auto-answer on --subjective on --master on")
    assert spec.resolved_tool == "yuketang_exam_set"
    assert spec.params["master"] == "on" and spec.params["slave"] == ""

    assert "整体停用" in _error("yuketang exam set --paper on")
    assert "没有可配置项" in _error("yuketang exam set --foo 1")

    spec = _no_error("yuketang exam set --x-access-token 'tok-12345678'")
    assert spec.resolved_tool == "yuketang_set_token"
    assert spec.params["x_access_token"] == "tok-12345678"

    assert "单独发送" in _error("yuketang exam set --x-access-token abc --llm on")
    assert "缺少令牌值" in _error("yuketang exam set --x-access-token")


def test_yuketang_unknown_commands() -> None:
    assert "未知命令" in _error("yuketang foo")
    assert "未知命令" in _error("yuketang lesson foo")
    assert "未知命令" in _error("yuketang exam foo")
    assert _spec("yuketang").is_help and _spec("yuketang lesson").is_help


def test_yuketang_help_tree() -> None:
    assert '先在雨课堂设置密码' in '\n'.join(build_help_payload('yuketang')['tips'])
    root = build_help_payload("")
    assert "yuketang status" in root["commands"]
    assert "help yuketang" in root["examples"]

    for topic in ("yuketang", "yuketang lesson", "yuketang exam"):
        payload = build_help_payload(topic)
        assert payload["topic"] == topic
        assert payload["commands"] and payload["tips"]

    exam_help = build_help_payload("yuketang exam")
    assert any("x-access-token" in command for command in exam_help["commands"])


def test_sensitive_redaction() -> None:
    masked = redact_cli_command("yuketang exam set --x-access-token 'SECRET-TOKEN-99'")
    assert "SECRET-TOKEN-99" not in masked
    assert "<redacted>" in masked

    params = redact_cli_params({"x_access_token": "SECRET", "op": "add"})
    assert params["x_access_token"] == "<redacted>"
    assert params["op"] == "add"


# ---------- 配置逻辑 ----------

def _with_config_file(test_body) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "yuketang_config.json"
        test_body(config_file)


def test_load_defaults_and_roundtrip() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        assert config["enabled"] is False
        assert config["domain"] == "www.yuketang.cn"
        assert config["lesson"]["autoEnter"] is True
        assert config["lesson"]["an"] is True
        assert config["lesson"]["llm"] is True
        assert config["lesson"]["enterDelay"] == 0
        assert config["lesson"]["subjective"] is False
        assert config["exam"]["subjective"] is False
        assert config["lesson"]["ppt"] is False and config["lesson"]["si"] is False
        assert config["exam"]["paper"] is False

        ycfg.save_config(config_file, config)
        reloaded = ycfg.load_config(config_file)
        assert reloaded == config

    _with_config_file(body)


def test_sanitize_forces_locked_keys() -> None:
    def body(config_file: Path) -> None:
        tampered = ycfg.default_config()
        tampered["lesson"]["ppt"] = True
        tampered["lesson"]["si"] = True
        tampered["exam"]["paper"] = True
        config_file.write_text(json.dumps(tampered), encoding="utf-8")

        config = ycfg.load_config(config_file)
        assert config["lesson"]["ppt"] is False
        assert config["lesson"]["si"] is False
        assert config["exam"]["paper"] is False

    _with_config_file(body)


def test_apply_lesson_set_persists() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_lesson_set(config, {
            "auto_answer": "on", "llm": "on", "subjective": "on", "enter_delay": "120",
        })
        assert result["success"], result["msg"]
        ycfg.save_config(config_file, config)

        reloaded = ycfg.load_config(config_file)
        assert reloaded["lesson"]["an"] is True
        assert reloaded["lesson"]["llm"] is True
        assert reloaded["lesson"]["subjective"] is True
        assert reloaded["lesson"]["enterDelay"] == 120
        assert "进班延时=120秒" in result["reply_text"]

    _with_config_file(body)


def test_apply_lesson_set_rejects_bad_values() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_lesson_set(config, {"auto_answer": "tomorrow"})
        assert not result["success"]
        result = ycfg.apply_lesson_set(config, {"enter_delay": "999"})
        assert not result["success"]
        assert config["lesson"]["an"] is True  # 默认开，失败操作不改值
        assert config["lesson"]["enterDelay"] == 0

    _with_config_file(body)


def test_auto_enter_toggle_persists_and_defaults_on() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        assert config["lesson"]["autoEnter"] is True

        result = ycfg.apply_lesson_set(config, {"auto_enter": "off"})
        assert result["success"], result["msg"]
        assert "自动进班=关" in result["reply_text"]
        assert "不再进班" in result["reply_text"]
        ycfg.save_config(config_file, config)
        assert ycfg.load_config(config_file)["lesson"]["autoEnter"] is False

        result = ycfg.apply_lesson_set(config, {"auto_enter": "开"})
        assert result["success"] and "自动进班=开" in result["reply_text"]
        assert not ycfg.apply_lesson_set(config, {"auto_enter": "maybe"})["success"]

        status = ycfg.build_status_result(config)
        assert "自动进班=开" in status["reply_text"]
        show = ycfg.build_config_show_result(config, "lesson")
        assert "自动进班=开" in show["reply_text"]

        legacy = json.loads(json.dumps({**config, "lesson": {k: v for k, v in config["lesson"].items() if k != "autoEnter"}}))
        assert ycfg.sanitize_config(legacy)["lesson"]["autoEnter"] is True

        # 存量配置缺键时按新默认补齐：自动进班/自动答题/大模型默认开
        stripped = ycfg.sanitize_config({"lesson": {"subjective": True}})
        assert stripped["lesson"]["autoEnter"] is True
        assert stripped["lesson"]["an"] is True
        assert stripped["lesson"]["llm"] is True
        assert stripped["lesson"]["subjective"] is True

    _with_config_file(body)


def test_apply_exam_set_master_slave_mutex() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_exam_set(config, {"master": "on", "slave": "on"})
        assert not result["success"]
        assert "不能同时开启" in result["msg"]
        assert config["exam"]["isMaster"] is False and config["exam"]["isSlave"] is False

        result = ycfg.apply_exam_set(config, {"master": "on"})
        assert result["success"]
        assert config["exam"]["isMaster"] is True and config["exam"]["isSlave"] is False

    _with_config_file(body)


def test_exam_set_warns_when_whitelist_empty() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_exam_set(config, {"auto_answer": "on"})
        assert result["success"]
        assert "整体关闭" in result["reply_text"]

    _with_config_file(body)


def test_list_update_and_exam_scope_warning() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_list_update(config, "exam_whitelist", "add", ["未央.机器学习"])
        assert result["success"]
        assert config["exam"]["classroomWhiteList"] == ["未央.机器学习"]

        result = ycfg.apply_list_update(config, "exam_whitelist", "clear", [])
        assert result["success"]
        assert config["exam"]["classroomWhiteList"] == []
        assert "整体关闭" in result["reply_text"]

        result = ycfg.apply_list_update(config, "lesson_blacklist", "add", ["A", "A", "B"])
        assert config["lesson"]["classroomBlackList"] == ["A", "B"]

        result = ycfg.apply_list_update(config, "lesson_blacklist", "remove", ["A", "missing"])
        assert result["success"]
        assert config["lesson"]["classroomBlackList"] == ["B"]
        assert "未找到" in result["reply_text"]

        result = ycfg.apply_list_update(config, "codes", "add", ["94RGB"])
        assert config["other"]["classroomCodeList"] == ["94RGB"]

    _with_config_file(body)


def test_start_time_parse_and_clear() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_start_time(config, "set", "未央.机器学习", "1=08:00,2=13:30")
        assert result["success"], result["msg"]
        assert config["lesson"]["classroomStartTimeDict"]["未央.机器学习"] == {"1": "08:00", "2": "13:30"}

        result = ycfg.apply_start_time(config, "set", "X", "8=08:00")
        assert not result["success"] and "1-7" in result["msg"]
        result = ycfg.apply_start_time(config, "set", "X", "1=25:00")
        assert not result["success"] and "HH:MM" in result["msg"]

        result = ycfg.apply_start_time(config, "clear", "未央.机器学习", "")
        assert result["success"]
        assert "未央.机器学习" not in config["lesson"]["classroomStartTimeDict"]

    _with_config_file(body)


def test_token_masking_never_leaks() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        token = "x-access-abcdef123456"
        result = ycfg.apply_token(config, token)
        assert result["success"]
        ycfg.save_config(config_file, config)

        stored_text = config_file.read_text(encoding="utf-8")
        assert token not in stored_text
        assert "enc:v2:" in stored_text

        reloaded = ycfg.load_config(config_file)
        assert reloaded["credentials"]["x_access_token"] == token

        redacted = ycfg.redacted_config(reloaded)
        assert token not in json.dumps(redacted, ensure_ascii=False)
        assert "3456" in redacted["credentials"]["x_access_token"]

        status = ycfg.build_status_result(reloaded)
        assert token not in json.dumps(status, ensure_ascii=False)
        assert "尾号 3456" in status["reply_text"]
        assert "守护进程：未接入" in status["reply_text"]

        empty = ycfg.build_status_result(ycfg.default_config())
        assert "未设置" in empty["reply_text"]

    _with_config_file(body)


def test_domain_apply_and_warning() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_domain(config, "pro")
        assert result["success"] and config["domain"] == "pro.yuketang.cn"
        assert "重新扫码" in result["reply_text"]

        result = ycfg.apply_domain(config, "not-a-domain")
        assert not result["success"]

        result = ycfg.apply_domain(config, "huanghe.yuketang.cn")
        assert result["success"] and config["domain"] == "huanghe.yuketang.cn"

    _with_config_file(body)


def test_enable_disable() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        assert ycfg.apply_enabled(config, "on")["success"] and config["enabled"] is True
        result = ycfg.apply_enabled(config, "off")
        assert result["success"] and config["enabled"] is False
        assert "保留" in result["reply_text"]
        assert not ycfg.apply_enabled(config, "maybe")["success"]

    _with_config_file(body)


def test_yuketang_logout_parse_and_apply() -> None:
    spec = _no_error("yuketang logout")
    assert spec.resolved_tool == "yuketang_logout" and spec.params == {}
    assert _no_error("yuketang 退出登录").resolved_tool == "yuketang_logout"

    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        ycfg.apply_enabled(config, "on")
        result = ycfg.apply_logout(config)
        assert result["success"], result["msg"]
        assert config["enabled"] is False
        assert config["credentials"]["account"] == ""  # 绑定凭据不动
        ycfg.save_config(config_file, config)
        assert ycfg.load_config(config_file)["enabled"] is False

    _with_config_file(body)


def test_yuketang_nl_match() -> None:
    login_cases = [
        "登录雨课堂", "登陆雨课堂", "帮我登录雨课堂", "请重新登录一下雨课堂",
        "雨课堂登录", "雨课堂重新登录。", "yuketang登录", "给我登录一下雨课堂账号",
    ]
    for text in login_cases:
        assert yuketang_nl.match_action(text) == "login", text

    logout_cases = [
        "退出登录雨课堂", "退出雨课堂", "雨课堂退出登录", "帮我注销雨课堂",
        "请退出一下雨课堂账号", "yuketang退出登录",
    ]
    for text in logout_cases:
        assert yuketang_nl.match_action(text) == "logout", text

    negative_cases = [
        "雨课堂登录失败怎么办", "怎么登录雨课堂", "雨课堂是什么",
        "今天雨课堂登录了吗", "帮我看看雨课堂登录状态", "登录教务系统",
        "退出群聊", "",
    ]
    for text in negative_cases:
        assert yuketang_nl.match_action(text) is None, text


# ---------- 账号密码登录命令族 ----------

def test_yuketang_account_set_parse() -> None:
    spec = _no_error("yuketang account set --account 13800001234 --password 'secret123'")
    assert spec.resolved_tool == "yuketang_account_set"
    assert spec.params == {"account": "13800001234", "password": "secret123"}
    assert "用法" in _error("yuketang account")
    assert "缺少必填参数" in _error("yuketang account set")
    assert "缺少必填参数" in _error("yuketang account set --account 13800001234")

    masked = redact_cli_command("yuketang account set --account 13800001234 --password 'secret123'")
    assert "secret123" not in masked


def test_yuketang_login_parse() -> None:
    spec = _no_error("yuketang login")
    assert spec.resolved_tool == "yuketang_login"
    assert spec.params == {"force": False}
    assert _no_error("yuketang 登录").resolved_tool == "yuketang_login"
    spec = _no_error("yuketang login --force")
    assert spec.resolved_tool == "yuketang_login" and spec.params == {"force": True}


def test_apply_account_set_and_redaction() -> None:
    def body(config_file: Path) -> None:
        config = ycfg.load_config(config_file)
        result = ycfg.apply_account_set(config, "13800001234", "pw123456")
        assert result["success"], result["msg"]
        ycfg.save_config(config_file, config)

        stored_text = config_file.read_text(encoding="utf-8")
        assert "13800001234" not in stored_text
        assert "pw123456" not in stored_text
        assert stored_text.count("enc:v2:") >= 2

        reloaded = ycfg.load_config(config_file)
        assert reloaded["credentials"]["account"] == "13800001234"
        assert reloaded["credentials"]["password"] == "pw123456"

        red = ycfg.redacted_config(reloaded)
        blob = json.dumps(red, ensure_ascii=False)
        assert "pw123456" not in blob
        assert "1234" in red["credentials"]["account"]
        assert red["credentials"]["password"] == "已设置"

        status = ycfg.build_status_result(reloaded)
        assert "pw123456" not in json.dumps(status, ensure_ascii=False)
        assert "1234" in status["reply_text"]

        assert not ycfg.apply_account_set(config, "", "x" * 8)["success"]
        assert not ycfg.apply_account_set(config, "13800001234", "123")["success"]

    _with_config_file(body)


def test_plaintext_credentials_are_migrated_on_read() -> None:
    def body(config_file: Path) -> None:
        raw = ycfg.default_config()
        raw["credentials"] = {
            "x_access_token": "legacy-token",
            "account": "13800001234",
            "password": "legacy-password",
        }
        config_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        loaded = ycfg.load_config(config_file)

        assert loaded["credentials"]["x_access_token"] == "legacy-token"
        assert loaded["credentials"]["account"] == "13800001234"
        assert loaded["credentials"]["password"] == "legacy-password"
        stored_text = config_file.read_text(encoding="utf-8")
        assert "legacy-token" not in stored_text
        assert "13800001234" not in stored_text
        assert "legacy-password" not in stored_text

    _with_config_file(body)



if __name__ == "__main__":
    failures = 0
    for name in sorted(dir()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    raise SystemExit(1 if failures else 0)
