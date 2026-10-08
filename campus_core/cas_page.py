from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class _CasLoginPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.execution = ""
        self.pwd_encrypt_salt = ""
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() != "input":
            return
        name = normalized.get("name", "")
        element_id = normalized.get("id", "")
        value = normalized.get("value", "")
        if name == "execution":
            self.execution = value
        if name == "pwdEncryptSalt" or element_id == "pwdEncryptSalt":
            self.pwd_encrypt_salt = value

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = str(data or "").strip()
        if not text:
            return
        self.text_parts.append(text)
        if self._in_title:
            self.title_parts.append(text)


def _parse_cas_page(html: Any) -> _CasLoginPageParser:
    parser = _CasLoginPageParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser


def extract_cas_login_fields(html: Any) -> dict[str, str]:
    parser = _parse_cas_page(html)
    return {
        "execution": parser.execution,
        "pwd_encrypt_salt": parser.pwd_encrypt_salt,
    }


def classify_cas_login_page(
    html: Any,
    *,
    status_code: int | None = None,
    final_url: str = "",
) -> tuple[str, str]:
    del final_url
    if status_code in {401, 403, 429}:
        return (
            "cas_blocked",
            f"统一认证入口拒绝了宿主机请求（HTTP {status_code}），请稍后重试或检查宿主机网络策略",
        )

    parser = _parse_cas_page(html)
    visible = " ".join([*parser.title_parts, *parser.text_parts]).lower()
    if any(marker in visible for marker in ("验证码", "安全验证", "captcha", "challenge", "滑块")):
        return (
            "cas_challenge_required",
            "统一认证要求验证码或额外验证，当前自动登录不能继续",
        )
    if not str(html or "").strip():
        return "cas_empty_response", "统一认证返回了空页面，无法继续登录"
    return (
        "cas_schema_incompatible",
        "统一认证返回页未找到登录所需字段；可能是额外验证页或当前解析器不兼容，不能仅凭此判断学校页面已改版",
    )
