from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin

KINGO_WARNING = (
    "当前使用 xk 独立登录，仅保证课表、选课状态和空教室等 xk 能力；"
    "无法为图书馆、研讨室、河宝提供 IDS 统一认证复用。"
)


@dataclass(frozen=True)
class AuthResult:
    success: bool
    mode: str = ""
    error_code: str = ""
    message: str = ""
    degraded: bool = False
    warning: str = ""
    ids_error_code: str = ""
    ids_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "degraded": self.degraded,
            "error_code": self.error_code,
            "message": self.message,
            "warning": self.warning,
            "ids_error_code": self.ids_error_code,
            "ids_message": self.ids_message,
        }


_SBOXES = (
    ((14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7), (0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8), (4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0), (15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13)),
    ((15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10), (3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5), (0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15), (13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9)),
    ((10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8), (13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1), (13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7), (1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12)),
    ((7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15), (13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9), (10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4), (3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14)),
    ((2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9), (14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6), (4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14), (11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3)),
    ((12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11), (10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8), (9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6), (4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13)),
    ((4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1), (13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6), (1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2), (6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12)),
    ((13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7), (1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2), (7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8), (2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11)),
)
_P_PERMUTE = (15, 6, 19, 20, 28, 11, 27, 16, 0, 14, 22, 25, 4, 17, 30, 9, 1, 7, 23, 13, 31, 26, 2, 8, 18, 12, 29, 5, 21, 10, 3, 24)
_FINAL_PERMUTE = (39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29, 36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25, 32, 0, 40, 8, 48, 16, 56, 24)
_PC2 = (13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9, 22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1, 40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47, 43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31)
_KEY_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)


def _text_bit_blocks(value: str) -> list[list[int]]:
    units = [ord(character) for character in str(value or "")]
    blocks: list[list[int]] = []
    for index in range(0, len(units), 4):
        chunk = units[index : index + 4] + [0] * (4 - len(units[index : index + 4]))
        blocks.append([int(bit) for unit in chunk for bit in f"{unit:016b}"])
    return blocks


def _round_keys(key_bits: list[int]) -> list[list[int]]:
    key = [key_bits[8 * (7 - column) + row] for row in range(7) for column in range(8)]
    result: list[list[int]] = []
    for shift in _KEY_SHIFTS:
        left, right = key[:28], key[28:]
        key = left[shift:] + left[:shift] + right[shift:] + right[:shift]
        result.append([key[index] for index in _PC2])
    return result


def _initial_permute(bits: list[int]) -> list[int]:
    result = [0] * 64
    for index in range(4):
        odd = 1 + index * 2
        even = index * 2
        for offset, source_row in enumerate(range(7, -1, -1)):
            result[index * 8 + offset] = bits[source_row * 8 + odd]
            result[index * 8 + offset + 32] = bits[source_row * 8 + even]
    return result


def _expand(bits: list[int]) -> list[int]:
    result: list[int] = []
    for index in range(8):
        start = index * 4
        result.extend((bits[start - 1 if index else 31], *bits[start : start + 4], bits[0 if index == 7 else start + 4]))
    return result


def _sbox(bits: list[int]) -> list[int]:
    result: list[int] = []
    for index, box in enumerate(_SBOXES):
        chunk = bits[index * 6 : index * 6 + 6]
        row = chunk[0] * 2 + chunk[5]
        column = chunk[1] * 8 + chunk[2] * 4 + chunk[3] * 2 + chunk[4]
        result.extend(int(bit) for bit in f"{box[row][column]:04b}")
    return result


def _encrypt_bits(data_bits: list[int], key_bits: list[int]) -> list[int]:
    permuted = _initial_permute(data_bits)
    left, right = permuted[:32], permuted[32:]
    for round_key in _round_keys(key_bits):
        expanded = [one ^ two for one, two in zip(_expand(right), round_key)]
        sbox_bits = _sbox(expanded)
        transformed = [sbox_bits[index] for index in _P_PERMUTE]
        left, right = right, [one ^ two for one, two in zip(transformed, left)]
    combined = right + left
    return [combined[index] for index in _FINAL_PERMUTE]


def _bits_to_hex(bits: list[int]) -> str:
    return "".join(f"{int(''.join(str(bit) for bit in bits[index:index + 4]), 2):X}" for index in range(0, 64, 4))


def kingo_str_enc(data: str, key: str) -> str:
    """Python-compatible port of Kingo's ``strEnc(data, key, null, null)``."""
    key_blocks = _text_bit_blocks(key)
    if not key_blocks:
        return ""

    encrypted: list[str] = []
    for block in _text_bit_blocks(data):
        current = block
        for key_block in key_blocks:
            current = _encrypt_bits(current, key_block)
        encrypted.append(_bits_to_hex(current))
    return "".join(encrypted)


def kingo_encode_params(raw_params: str, temp_key: str) -> str:
    encrypted_hex = kingo_str_enc(raw_params, temp_key)
    return base64.b64encode(encrypted_hex.encode("utf-8")).decode("ascii")


def kingo_token(raw_params: str, timestamp: str) -> str:
    raw_md5 = hashlib.md5(raw_params.encode("utf-8")).hexdigest()
    time_md5 = hashlib.md5(timestamp.encode("utf-8")).hexdigest()
    return hashlib.md5(f"{raw_md5}{time_md5}".encode("utf-8")).hexdigest()


def password_expression(password: str) -> int:
    result = 0
    for character in password:
        code = ord(character)
        if 48 <= code <= 57:
            result |= 8
        elif 97 <= code <= 122:
            result |= 4
        elif 65 <= code <= 90:
            result |= 2
        else:
            result |= 1
    return result


def _input_value(text: str, name: str) -> str:
    patterns = (
        rf'<input[^>]*(?:id|name)=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)',
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*(?:id|name)=["\']{re.escape(name)}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


def _javascript_value(text: str, name: str) -> str:
    match = re.search(
        rf'var\s+{re.escape(name)}\s*=\s*["\']([^"\']*)["\']',
        text,
        re.I,
    )
    return match.group(1) if match else ""


def _response_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("message") or payload.get("msg") or "").strip()


def _failure_code(message: str, status: str = "") -> str:
    compact = re.sub(r"\s+", "", str(message or "")).lower()
    if status == "505" or any(marker in compact for marker in ("验证码", "captcha", "校验码")):
        return "captcha_required"
    if any(marker in compact for marker in ("账号或密码有误", "密码错误", "密码不正确", "用户名或密码")):
        return "invalid_credentials"
    return "service_error"


def login_via_kingo(
    *,
    session: Any,
    base_url: str,
    username: str,
    password: str,
    decode_text: Callable[[Any], str],
    check_logged_in: Callable[[], bool],
) -> AuthResult:
    try:
        login_page = session.get(
            urljoin(f"{base_url}/", "cas/login.action"),
            allow_redirects=True,
            timeout=30,
        )
        login_text = decode_text(login_page)
    except Exception as exc:
        return AuthResult(False, error_code="network_error", message=f"xk 登录页访问失败: {exc}")

    session_id = _javascript_value(login_text, "_ssessionid") or _javascript_value(
        login_text, "_sessionid"
    )
    hid_flag = _input_value(login_text, "hid_flag")
    randnumber = _input_value(login_text, "randnumber")
    if not session_id:
        return AuthResult(False, error_code="protocol_error", message="xk 登录页缺少会话标识")
    if hid_flag != "1" and not randnumber:
        return AuthResult(False, error_code="captcha_required", message="xk 独立登录需要验证码")

    try:
        temp_key = decode_text(
            session.get(
                urljoin(f"{base_url}/", "frame/homepage?method=getTempDeskey"),
                timeout=20,
            )
        ).strip()
        timestamp = decode_text(
            session.get(
                urljoin(f"{base_url}/", "frame/homepage?method=getTempNowtime"),
                timeout=20,
            )
        ).strip()
    except Exception as exc:
        return AuthResult(False, error_code="network_error", message=f"xk 动态登录参数获取失败: {exc}")

    if not temp_key or not timestamp:
        return AuthResult(False, error_code="protocol_error", message="xk 动态登录参数缺失")

    encoded_username = base64.b64encode(f"{username};;{session_id}".encode("utf-8")).decode(
        "ascii"
    )
    contains_username = "1" if username.strip().lower() in password.strip().lower() else "0"
    password_policy = "1" if password and username != password and len(password) >= 6 else "0"
    raw_params = (
        f"_u{randnumber}={encoded_username}"
        f"&_p{randnumber}={password}"
        f"&randnumber={randnumber}"
        f"&isPasswordPolicy={password_policy}"
        f"&txt_mm_expression={password_expression(password)}"
        f"&txt_mm_length={len(password)}"
        f"&txt_mm_userzh={contains_username}"
        f"&hid_flag={hid_flag}"
        "&hidlag=1"
        "&hid_dxyzm="
    )
    form_data = {
        "params": kingo_encode_params(raw_params, temp_key),
        "token": kingo_token(raw_params, timestamp),
        "timestamp": timestamp,
        "deskey": "",
        "ssessionid": session_id,
    }

    try:
        response = session.post(
            urljoin(f"{base_url}/", "cas/logon.action"),
            data=form_data,
            headers={"X-Requested-With": "XMLHttpRequest"},
            allow_redirects=True,
            timeout=35,
        )
        payload = json.loads(decode_text(response))
    except json.JSONDecodeError:
        return AuthResult(False, error_code="protocol_error", message="xk 登录响应不是有效 JSON")
    except Exception as exc:
        return AuthResult(False, error_code="network_error", message=f"xk 登录请求失败: {exc}")

    status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
    message = _response_message(payload)
    if status != "200":
        return AuthResult(False, error_code=_failure_code(message, status), message=message or "xk 登录失败")

    result_path = str(payload.get("result") or "")
    try:
        if result_path:
            session.get(urljoin(f"{base_url}/", result_path), timeout=30)
    except Exception as exc:
        return AuthResult(False, error_code="network_error", message=f"xk 登录后跳转失败: {exc}")

    if not check_logged_in():
        return AuthResult(False, error_code="service_error", message="xk 返回成功但未建立登录态")
    return AuthResult(
        True,
        mode="xk_kingo",
        degraded=True,
        message="xk 独立登录成功",
        warning=KINGO_WARNING,
    )
