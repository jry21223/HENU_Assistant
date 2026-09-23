"""雨课堂（yuketang）per-QQ 配置：默认值、校验、变更与脱敏视图。

存储文件 yuketang_config.json 由 PluginStorageAdapter 事务化装载/回写，
本模块只做纯逻辑，不依赖 LangBot SDK。ppt/si/paper 为写保护键，永远 False。
原版 config.json 的 lesson/exam/other 内层键名保持不变，守护进程可直接消费；
新增 lesson.enterDelay（进班延时秒）与 lesson/exam 的 subjective（主观题开关）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DOMAIN_ALIASES = {
    "www": "www.yuketang.cn",
    "雨课堂": "www.yuketang.cn",
    "pro": "pro.yuketang.cn",
    "荷塘": "pro.yuketang.cn",
    "changjiang": "changjiang.yuketang.cn",
    "长江": "changjiang.yuketang.cn",
    "huanghe": "huanghe.yuketang.cn",
    "黄河": "huanghe.yuketang.cn",
}
VALID_DOMAINS = sorted(set(DOMAIN_ALIASES.values()))

# 写保护键：课件 PDF / PPT 进度 / 试卷文件推送已整体停用。
FORBIDDEN_LOCKED_KEYS = ("ppt", "si", "paper")
LOCKED_MESSAGE = "课件 PDF、PPT 进度、试卷文件推送已整体停用，不可配置。"

MAX_ENTER_DELAY_SECONDS = 600
MAX_TOKEN_LENGTH = 512
MAX_COURSE_NAME_LENGTH = 64

_WEEKDAY_RE = re.compile(r"^[1-7]$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_LIST_SCOPES = {
    "lesson_whitelist": ("lesson", "classroomWhiteList", "课程白名单"),
    "lesson_blacklist": ("lesson", "classroomBlackList", "课程黑名单"),
    "exam_whitelist": ("exam", "classroomWhiteList", "考试白名单"),
    "codes": ("other", "classroomCodeList", "班级邀请码/课堂暗号"),
}

_ON_VALUES = {"1", "true", "yes", "on", "开", "开启", "是"}
_OFF_VALUES = {"0", "false", "no", "off", "关", "关闭", "否"}


def default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "domain": "www.yuketang.cn",
        "lesson": {
            "classroomWhiteList": [],
            "classroomBlackList": [],
            "classroomStartTimeDict": {},
            "llm": False,
            "an": False,
            "ppt": False,
            "si": False,
            "enterDelay": 0,
            "subjective": False,
        },
        "exam": {
            "classroomWhiteList": [],
            "llm": False,
            "an": False,
            "paper": False,
            "isMaster": False,
            "isSlave": False,
            "subjective": False,
        },
        "other": {"classroomCodeList": []},
        "credentials": {"x_access_token": "", "account": "", "password": ""},
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _ON_VALUES:
        return True
    if text in _OFF_VALUES:
        return False
    return default


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _clean_start_time_dict(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for course, slots in value.items():
        course_name = str(course).strip()
        if not course_name or not isinstance(slots, dict):
            continue
        clean: dict[str, str] = {}
        for weekday, timing in slots.items():
            day_text = str(weekday).strip()
            time_text = str(timing).strip()
            if _WEEKDAY_RE.match(day_text) and _TIME_RE.match(time_text):
                clean[day_text] = time_text
        if clean:
            result[course_name] = clean
    return result


def sanitize_config(raw: Any) -> dict[str, Any]:
    """将存储 JSON 归并为合法形状：丢弃未知键、强制写保护键为 False。"""
    base = default_config()
    if not isinstance(raw, dict):
        return base

    base["enabled"] = _coerce_bool(raw.get("enabled"), False)
    base["domain"] = _normalize_domain_value(raw.get("domain")) or base["domain"]

    lesson = raw.get("lesson") if isinstance(raw.get("lesson"), dict) else {}
    base["lesson"]["classroomWhiteList"] = _clean_str_list(lesson.get("classroomWhiteList"))
    base["lesson"]["classroomBlackList"] = _clean_str_list(lesson.get("classroomBlackList"))
    base["lesson"]["classroomStartTimeDict"] = _clean_start_time_dict(lesson.get("classroomStartTimeDict"))
    base["lesson"]["llm"] = _coerce_bool(lesson.get("llm"), False)
    base["lesson"]["an"] = _coerce_bool(lesson.get("an"), False)
    base["lesson"]["enterDelay"] = _clamp_enter_delay(lesson.get("enterDelay"))
    base["lesson"]["subjective"] = _coerce_bool(lesson.get("subjective"), False)

    exam = raw.get("exam") if isinstance(raw.get("exam"), dict) else {}
    base["exam"]["classroomWhiteList"] = _clean_str_list(exam.get("classroomWhiteList"))
    base["exam"]["llm"] = _coerce_bool(exam.get("llm"), False)
    base["exam"]["an"] = _coerce_bool(exam.get("an"), False)
    base["exam"]["isMaster"] = _coerce_bool(exam.get("isMaster"), False)
    base["exam"]["isSlave"] = _coerce_bool(exam.get("isSlave"), False)
    base["exam"]["subjective"] = _coerce_bool(exam.get("subjective"), False)

    other = raw.get("other") if isinstance(raw.get("other"), dict) else {}
    base["other"]["classroomCodeList"] = _clean_str_list(other.get("classroomCodeList"))

    credentials = raw.get("credentials") if isinstance(raw.get("credentials"), dict) else {}
    token = str(credentials.get("x_access_token") or "").strip()
    base["credentials"]["x_access_token"] = token[:MAX_TOKEN_LENGTH]
    base["credentials"]["account"] = str(credentials.get("account") or "").strip()[:64]
    base["credentials"]["password"] = str(credentials.get("password") or "")[:128]
    return base


def load_config(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        return default_config()
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"读取雨课堂配置失败（不当作空配置处理）: {exc}") from exc
    return sanitize_config(raw)


def save_config(config_file: Path, config: Any) -> None:
    payload = sanitize_config(config)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_file = config_file.with_name(config_file.name + ".tmp")
    temp_file.write_text(text, encoding="utf-8")
    os.replace(temp_file, config_file)


def _normalize_domain_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[text]
    if text in VALID_DOMAINS:
        return text
    return ""


def _clamp_enter_delay(value: Any) -> int:
    try:
        delay = int(str(value if value is not None else 0).strip() or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_ENTER_DELAY_SECONDS, delay))


def _parse_on_off(value: Any, flag_name: str) -> tuple[bool | None, str]:
    text = str(value if value is not None else "").strip().lower()
    if text in _ON_VALUES:
        return True, ""
    if text in _OFF_VALUES:
        return False, ""
    return None, f"参数 `--{flag_name}` 需要 on/off（开/关），收到: {value!r}"


def _parse_enter_delay(value: Any) -> tuple[int | None, str]:
    text = str(value if value is not None else "").strip()
    try:
        delay = int(text)
    except (TypeError, ValueError):
        return None, f"参数 `--enter-delay` 需要是 0-{MAX_ENTER_DELAY_SECONDS} 的整数秒数。"
    if delay < 0 or delay > MAX_ENTER_DELAY_SECONDS:
        return None, f"参数 `--enter-delay` 取值范围 0-{MAX_ENTER_DELAY_SECONDS} 秒。"
    return delay, ""


def parse_slots(text: Any) -> tuple[dict[str, str] | None, str]:
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None, "参数 `--slots` 不能为空，示例: \"1=08:00,2=13:30\"（星期 1-7=HH:MM）。"
    slots: dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        key, has_value, value = item.partition("=")
        weekday = key.strip()
        timing = value.strip() if has_value else ""
        if not _WEEKDAY_RE.match(weekday):
            return None, f"星期编号必须是 1-7，收到: {key.strip()!r}。"
        if not _TIME_RE.match(timing):
            return None, f"时间格式必须是 HH:MM（00:00-23:59），收到: {timing!r}。"
        slots[weekday] = timing
    if not slots:
        return None, "参数 `--slots` 没有解析到任何条目，示例: \"1=08:00,2=13:30\"。"
    return slots, ""


def _clean_course_names(items: Any) -> tuple[list[str], str]:
    names: list[str] = []
    for item in items or []:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > MAX_COURSE_NAME_LENGTH:
            return [], f"名称过长（>{MAX_COURSE_NAME_LENGTH} 字符）: {text[:24]}…"
        if text not in names:
            names.append(text)
    if not names:
        return [], "缺少名称参数。"
    return names, ""


def mask_token(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return "未设置"
    if len(text) <= 8:
        return "已设置"
    return f"已设置（尾号 {text[-4:]}）"


def _on_off_text(value: bool) -> str:
    return "开" if value else "关"


def redacted_config(config: dict[str, Any]) -> dict[str, Any]:
    """返回可安全进模型/日志的配置视图：凭据脱敏，写保护键照实展示为 false。"""
    safe = sanitize_config(config)
    account = safe["credentials"]["account"]
    safe["credentials"] = {
        "x_access_token": mask_token(safe["credentials"]["x_access_token"]),
        "account": (f"已设置（尾号 {account[-4:]}）" if account else "未设置"),
        "password": ("已设置" if safe["credentials"]["password"] else "未设置"),
    }
    return safe


def _lesson_summary(lesson: dict[str, Any]) -> str:
    parts = [
        f"自动答题={_on_off_text(lesson['an'])}",
        f"大模型={_on_off_text(lesson['llm'])}",
        f"主观题={_on_off_text(lesson['subjective'])}",
        f"进班延时={lesson['enterDelay']}秒",
    ]
    return " · ".join(parts)


def _exam_summary(exam: dict[str, Any]) -> str:
    master_slave = "主控" if exam["isMaster"] else ("从控" if exam["isSlave"] else "无")
    parts = [
        f"自动答题={_on_off_text(exam['an'])}",
        f"大模型={_on_off_text(exam['llm'])}",
        f"主观题={_on_off_text(exam['subjective'])}",
        f"主从={master_slave}",
    ]
    return " · ".join(parts)


def _list_text(items: list[str]) -> str:
    return "、".join(items) if items else "（空）"


def build_status_result(config: dict[str, Any]) -> dict[str, Any]:
    cfg = sanitize_config(config)
    lines = [
        f"雨课堂监听：{'启用' if cfg['enabled'] else '未启用'}",
        f"域名：{cfg['domain']}",
        f"课堂：{_lesson_summary(cfg['lesson'])}",
        f"课堂名单：白名单={_list_text(cfg['lesson']['classroomWhiteList'])}；黑名单={_list_text(cfg['lesson']['classroomBlackList'])}",
        f"考试：{_exam_summary(cfg['exam'])}",
        f"考试白名单：{_list_text(cfg['exam']['classroomWhiteList'])}"
        + ("（为空，考试功能整体关闭）" if not cfg["exam"]["classroomWhiteList"] else ""),
        f"邀请码：{_list_text(cfg['other']['classroomCodeList'])}",
        f"考试令牌：{mask_token(cfg['credentials']['x_access_token'])}",
        f"雨课堂账号：{('尾号 ' + cfg['credentials']['account'][-4:]) if cfg['credentials']['account'] else '未绑定（yuketang account set，仅私聊）'}",
        "守护进程：未接入（登录状态与课程列表待桥接后提供）",
    ]
    return {
        "success": True,
        "msg": "yuketang 配置状态",
        "reply_text": "\n".join(lines),
        "yuketang": redacted_config(cfg),
    }


def build_config_show_result(config: dict[str, Any], topic: str) -> dict[str, Any]:
    cfg = sanitize_config(config)
    topic = str(topic or "").strip().lower()
    if topic in {"", "all", "全部"}:
        payload = redacted_config(cfg)
        lines = [
            f"监听：{'启用' if cfg['enabled'] else '未启用'}；域名：{cfg['domain']}",
            f"课堂：{_lesson_summary(cfg['lesson'])}",
            f"课堂白名单：{_list_text(cfg['lesson']['classroomWhiteList'])}",
            f"课堂黑名单：{_list_text(cfg['lesson']['classroomBlackList'])}",
            f"进班时间表：{json.dumps(cfg['lesson']['classroomStartTimeDict'], ensure_ascii=False) or '{}'}",
            f"考试：{_exam_summary(cfg['exam'])}",
            f"考试白名单：{_list_text(cfg['exam']['classroomWhiteList'])}",
            f"邀请码：{_list_text(cfg['other']['classroomCodeList'])}",
            f"考试令牌：{mask_token(cfg['credentials']['x_access_token'])}",
            f"课件/PPT进度/试卷推送：已整体停用（{','.join(FORBIDDEN_LOCKED_KEYS)} 固定为 false）",
        ]
    elif topic in {"lesson", "课堂"}:
        payload = {"lesson": redacted_config(cfg)["lesson"]}
        lines = [
            f"课堂：{_lesson_summary(cfg['lesson'])}",
            f"白名单：{_list_text(cfg['lesson']['classroomWhiteList'])}",
            f"黑名单：{_list_text(cfg['lesson']['classroomBlackList'])}",
            f"进班时间表：{json.dumps(cfg['lesson']['classroomStartTimeDict'], ensure_ascii=False) or '{}'}",
        ]
    elif topic in {"exam", "考试"}:
        payload = {"exam": redacted_config(cfg)["exam"]}
        lines = [
            f"考试：{_exam_summary(cfg['exam'])}",
            f"白名单：{_list_text(cfg['exam']['classroomWhiteList'])}"
            + ("（为空，考试功能整体关闭）" if not cfg["exam"]["classroomWhiteList"] else ""),
            f"考试令牌：{mask_token(cfg['credentials']['x_access_token'])}",
        ]
    elif topic in {"other", "其他"}:
        payload = {"other": redacted_config(cfg)["other"]}
        lines = [f"邀请码：{_list_text(cfg['other']['classroomCodeList'])}"]
    else:
        return {
            "success": False,
            "msg": f"未知配置主题 `{topic}`，支持 lesson/exam/other。",
            "next_hint": "yuketang config show lesson",
        }
    return {
        "success": True,
        "msg": "yuketang 配置详情",
        "reply_text": "\n".join(lines),
        "yuketang": payload,
    }


def _result(msg: str, reply_text: str, config: dict[str, Any], success: bool = True) -> dict[str, Any]:
    return {
        "success": success,
        "msg": msg,
        "reply_text": reply_text,
        "yuketang": redacted_config(config),
    }


def apply_enabled(config: dict[str, Any], enabled: Any) -> dict[str, Any]:
    value, error = _parse_on_off(enabled, "enabled")
    if error:
        return _result(error, error, config, success=False)
    config["enabled"] = bool(value)
    state = "启用" if value else "停用"
    note = "" if value else "（配置与登录凭据保留，守护进程不再扫描该账号）"
    return _result(f"雨课堂监听已{state}", f"雨课堂监听已{state}{note}", config)


def apply_domain(config: dict[str, Any], domain: Any) -> dict[str, Any]:
    canonical = _normalize_domain_value(domain)
    if not canonical:
        options = "、".join(VALID_DOMAINS)
        msg = f"无效域名 `{domain}`。可选: {options}（或别名 www/pro/changjiang/huanghe）。"
        return _result(msg, msg, config, success=False)
    previous = config.get("domain")
    config["domain"] = canonical
    reply = f"域名已切换为 {canonical}"
    if previous and previous != canonical:
        reply += f"（原 {previous} 的 cookie 与课程列表将作废，需重新扫码登录）"
    return _result(f"域名切换为 {canonical}", reply, config)


def apply_lesson_set(config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    lesson = config["lesson"]
    changed: list[str] = []

    if params.get("auto_answer") not in (None, ""):
        value, error = _parse_on_off(params.get("auto_answer"), "auto-answer")
        if error:
            return _result(error, error, config, success=False)
        lesson["an"] = value
        changed.append(f"自动答题={_on_off_text(value)}")

    if params.get("llm") not in (None, ""):
        value, error = _parse_on_off(params.get("llm"), "llm")
        if error:
            return _result(error, error, config, success=False)
        lesson["llm"] = value
        changed.append(f"大模型={_on_off_text(value)}")

    if params.get("subjective") not in (None, ""):
        value, error = _parse_on_off(params.get("subjective"), "subjective")
        if error:
            return _result(error, error, config, success=False)
        lesson["subjective"] = value
        changed.append(f"主观题={_on_off_text(value)}")

    if params.get("enter_delay") not in (None, ""):
        value, error = _parse_enter_delay(params.get("enter_delay"))
        if error:
            return _result(error, error, config, success=False)
        lesson["enterDelay"] = value
        changed.append(f"进班延时={value}秒")

    if not changed:
        msg = "没有可更新的课堂配置项（支持 --auto-answer/--llm/--subjective/--enter-delay）。"
        return _result(msg, msg, config, success=False)

    reply = "已更新课堂配置：" + " · ".join(changed)
    if lesson["an"] and not lesson["llm"]:
        reply += "\n提示：自动答题已开但大模型生成未开，无答案时会提交默认答案。"
    return _result("已更新课堂配置", reply, config)


def apply_exam_set(config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    exam = config["exam"]
    changed: list[str] = []

    for param_key, flag_name, config_key in (
        ("auto_answer", "auto-answer", "an"),
        ("llm", "llm", "llm"),
        ("subjective", "subjective", "subjective"),
    ):
        if params.get(param_key) not in (None, ""):
            value, error = _parse_on_off(params.get(param_key), flag_name)
            if error:
                return _result(error, error, config, success=False)
            exam[config_key] = value
            changed.append(f"{flag_name}={_on_off_text(value)}")

    requested_master = exam["isMaster"]
    requested_slave = exam["isSlave"]
    if params.get("master") not in (None, ""):
        value, error = _parse_on_off(params.get("master"), "master")
        if error:
            return _result(error, error, config, success=False)
        requested_master = value
        changed.append(f"master={_on_off_text(value)}")
    if params.get("slave") not in (None, ""):
        value, error = _parse_on_off(params.get("slave"), "slave")
        if error:
            return _result(error, error, config, success=False)
        requested_slave = value
        changed.append(f"slave={_on_off_text(value)}")

    if requested_master and requested_slave:
        msg = "isMaster 与 isSlave 不能同时开启：主控答题时从控会同步提交相同答案，同开自相矛盾。"
        return _result(msg, msg, config, success=False)
    exam["isMaster"] = requested_master
    exam["isSlave"] = requested_slave

    if not changed:
        msg = "没有可更新的考试配置项（支持 --auto-answer/--llm/--subjective/--master/--slave）。"
        return _result(msg, msg, config, success=False)

    reply = "已更新考试配置：" + " · ".join(changed)
    if not exam["classroomWhiteList"]:
        reply += "\n注意：考试白名单为空，考试功能整体关闭；用 `yuketang exam whitelist add <课程名>` 开启。"
    return _result("已更新考试配置", reply, config)


def apply_list_update(config: dict[str, Any], scope: str, op: str, items: Any) -> dict[str, Any]:
    scope = str(scope or "").strip().lower()
    if scope not in _LIST_SCOPES:
        msg = f"未知名单范围 `{scope}`，支持 lesson_whitelist/lesson_blacklist/exam_whitelist/codes。"
        return _result(msg, msg, config, success=False)
    section_key, list_key, label = _LIST_SCOPES[scope]
    target = config[section_key][list_key]

    op = str(op or "").strip().lower()
    if op not in {"add", "remove", "clear"}:
        msg = f"未知操作 `{op}`，支持 add/remove/clear。"
        return _result(msg, msg, config, success=False)

    notes: list[str] = []
    if op == "clear":
        if items:
            msg = "`clear` 不接受额外名称参数。"
            return _result(msg, msg, config, success=False)
        if not target:
            notes.append("原本就为空")
        target.clear()
        summary = f"{label}已清空"
    else:
        names, error = _clean_course_names(items)
        if error:
            return _result(f"{label}{op} 失败: {error}", f"{label}{op} 失败: {error}", config, success=False)
        if op == "add":
            added = [name for name in names if name not in target]
            skipped = [name for name in names if name in target]
            target.extend(added)
            if skipped:
                notes.append(f"已存在跳过: {_list_text(skipped)}")
            if not added:
                notes.append("没有新增条目")
        else:
            removed = [name for name in names if name in target]
            missing = [name for name in names if name not in target]
            config[section_key][list_key] = [item for item in target if item not in names]
            if missing:
                notes.append(f"未找到: {_list_text(missing)}")
            if not removed:
                notes.append("没有移除条目")
        summary = f"{label}已{ '新增' if op == 'add' else '移除' }"

    reply = summary + f"：{_list_text(config[section_key][list_key])}"
    for note in notes:
        reply += f"（{note}）"
    if scope == "exam_whitelist" and not config["exam"]["classroomWhiteList"]:
        reply += "\n注意：考试白名单为空，考试功能整体关闭。"
    return _result(summary, reply, config)


def apply_start_time(config: dict[str, Any], op: str, course: Any, slots: Any) -> dict[str, Any]:
    op = str(op or "").strip().lower()
    if op not in {"set", "clear"}:
        msg = f"未知操作 `{op}`，支持 set/clear。"
        return _result(msg, msg, config, success=False)

    course_name = str(course or "").strip()
    if not course_name:
        msg = "缺少参数 `--course <课程名>`。"
        return _result(msg, msg, config, success=False)
    if len(course_name) > MAX_COURSE_NAME_LENGTH:
        msg = f"课程名过长（>{MAX_COURSE_NAME_LENGTH} 字符）。"
        return _result(msg, msg, config, success=False)

    table: dict[str, dict[str, str]] = config["lesson"]["classroomStartTimeDict"]
    if op == "clear":
        if course_name not in table:
            msg = f"课程 `{course_name}` 没有进班时间表条目。"
            return _result(msg, msg, config, success=False)
        table.pop(course_name)
        return _result("已清除进班时间表", f"已清除 `{course_name}` 的进班时间表。", config)

    parsed, error = parse_slots(slots)
    if error:
        return _result(error, error, config, success=False)
    table[course_name] = parsed
    pretty = "、".join(f"周{day} {timing}" for day, timing in parsed.items())
    return _result("已更新进班时间表", f"`{course_name}` 进班时间表：{pretty}（早于该时间不进班；与进班延时相互独立）。", config)


def apply_account_set(config: dict[str, Any], account: Any, password: Any) -> dict[str, Any]:
    account_text = str(account or "").strip()
    password_text = str(password if password is not None else "")
    if not account_text or len(account_text) < 5 or len(account_text) > 64:
        msg = "缺少或非法 `--account <手机号>`。"
        return _result(msg, msg, config, success=False)
    if not password_text or len(password_text) < 6 or len(password_text) > 128:
        msg = "缺少或非法 `--password '<密码>'`（6-128 字符）。"
        return _result(msg, msg, config, success=False)
    config["credentials"]["account"] = account_text
    config["credentials"]["password"] = password_text
    reply = (
        f"雨课堂账号已绑定（尾号 {account_text[-4:]}），凭据仅存于你的个人 Storage 并加密同步守护进程。\n"
        "下一步：发送 `yuketang login` 完成登录（自动过验证码，约 1 分钟）。"
    )
    return _result("已绑定雨课堂账号", reply, config)


def apply_token(config: dict[str, Any], token: Any) -> dict[str, Any]:
    text = str(token if token is not None else "").strip()
    if not text:
        msg = "缺少参数 `--x-access-token '<令牌>'`。"
        return _result(msg, msg, config, success=False)
    if len(text) > MAX_TOKEN_LENGTH:
        msg = f"令牌过长（>{MAX_TOKEN_LENGTH} 字符），疑似粘贴错误。"
        return _result(msg, msg, config, success=False)
    config["credentials"]["x_access_token"] = text
    return _result(
        "已保存考试令牌",
        f"考试令牌{mask_token(text)}，仅存于你的个人 Storage，展示时脱敏；考试系统将直接使用该令牌，不再另行生成。",
        config,
    )
