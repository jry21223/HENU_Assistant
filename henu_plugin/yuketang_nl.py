"""雨课堂自然语言短语匹配：识别登录/退出登录意图。

仅用于私聊敏感命令通道（identity-capture 拦截器）在进模型前直接执行，
采用整句锚定匹配，避免普通聊天里提到"登录"被误触发登录动作。
复用 identity_capture 的"正则识别 + 私聊直处理"机制。
"""
from __future__ import annotations

import re

_YKT = r"(?:雨课堂|yuketang)"
_POLITE = r"(?:请|帮我|麻烦你?|给我)?"
_TAIL = r"[。.！!~？?，,～\s]*"

# 登录雨课堂 / 帮我重新登录一下雨课堂 / 雨课堂登录。/ yuketang登录
_LOGIN_RE = re.compile(
    rf"^{_POLITE}(?:重新|再次)?(?:登录|登陆|登入)一?下?{_YKT}(?:账号|平台|课堂)?(?:哦|呀|哈)?{_TAIL}$"
    rf"|^{_YKT}(?:账号)?(?:重新|再次)?(?:登录|登陆|登入)一?下?(?:哦|呀|哈)?{_TAIL}$"
)
# 退出登录雨课堂 / 帮我退出雨课堂 / 雨课堂退出登录 / 注销雨课堂
_LOGOUT_RE = re.compile(
    rf"^{_POLITE}(?:退出登录|退出登陆|注销登录|退出|注销)一?下?{_YKT}(?:账号|平台|课堂)?(?:哦|呀|哈)?{_TAIL}$"
    rf"|^{_YKT}(?:账号)?(?:退出登录|退出登陆|注销登录|退出|注销)一?下?(?:哦|呀|哈)?{_TAIL}$"
)


def match_action(text: str) -> str | None:
    """返回 "login" / "logout" / None。"""
    compact = " ".join(str(text or "").lower().split())
    if not compact:
        return None
    if _LOGOUT_RE.match(compact):
        return "logout"
    if _LOGIN_RE.match(compact):
        return "login"
    return None
