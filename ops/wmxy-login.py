"""一次性登录：用 App 的设备身份完成密码登录，写入电费上下文。

密码只从环境变量读取，不写盘。设备身份来自 Frida 只读得到的 deviceId（见
docs/electricity-readonly-verification.md「全自动续期」）。

用法：
  WMXY_ACCOUNT=<账号> WMXY_PASSWORD=<密码> \
    python ops/wmxy-login.py --context output/private/electricity.json \
    --device output/private/app-device.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from campus_core.atomic_io import atomic_write_json  # noqa: E402
from campus_core.electricity_client import EXCHANGE_HEADERS, EXCHANGE_PARAMS, PAY_EXCHANGE, _merge_cookie  # noqa: E402
from campus_core.wmxy_auth import AuthError, exchange_secret, login_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--device", type=Path, required=True, help="含 id/model/product/release 的私有 JSON")
    args = parser.parse_args()
    account = os.environ.get("WMXY_ACCOUNT", "").strip()
    password = os.environ.get("WMXY_PASSWORD", "")
    if not account or not password:
        print("需要 WMXY_ACCOUNT 和 WMXY_PASSWORD 环境变量。", file=sys.stderr)
        return 1
    device_info = json.loads(args.device.read_text(encoding="utf-8"))
    context = json.loads(args.context.read_text(encoding="utf-8")) if args.context.exists() else {}
    device = {"deviceId": device_info["id"],
              "telephoneModel": device_info.get("model") or "sdk_gphone_arm64",
              "shebeixinghao": device_info.get("product") or "sdk_gphone_arm64",
              "telephoneInfo": device_info.get("release") or "11"}
    try:
        with httpx.Client(timeout=20, trust_env=False) as http:
            session = exchange_secret(http)
            user = login_password(http, account=account, password=password, session=session, device=device)
            custom = str(user.get("customId") or "")
            # 先落盘 token：登录会轮换旧 token，后续换票失败也不能丢。
            atomic_write_json(args.context, {
                **context, "session_id": session.session, "custom_id": custom,
                "login_token": str(user.get("token") or ""), "user_id": str(user.get("id") or ""),
                "login_name": str(user.get("mobile") or account), "device_id": device_info["id"],
                "device_model": device["telephoneModel"], "device_product": device["shebeixinghao"],
                "device_release": device["telephoneInfo"],
            })
            try:
                page = http.get(PAY_EXCHANGE, params={"customerId": custom or "1", "token": session.session,
                                                      **EXCHANGE_PARAMS},
                                headers=EXCHANGE_HEADERS, follow_redirects=False)
                if page.headers.get("location"):
                    http.get(httpx.URL(PAY_EXCHANGE).join(page.headers["location"]),
                             headers=EXCHANGE_HEADERS, follow_redirects=True)
                cookie = _merge_cookie("", {c.name: c.value for c in http.cookies.jar})
            except httpx.HTTPError:
                print("登录成功但换票失败，token 已保存，稍后可重试查询。", file=sys.stderr)
                return 0
    except AuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    atomic_write_json(args.context, {
        **context, "cookie": cookie, "session_id": session.session, "custom_id": custom,
        "login_token": str(user.get("token") or ""), "user_id": str(user.get("id") or ""),
        "login_name": str(user.get("mobile") or account), "device_id": device_info["id"],
        "device_model": device["telephoneModel"], "device_product": device["shebeixinghao"],
        "device_release": device["telephoneInfo"],
    })
    print(f"已写入 {args.context}（token 与设备身份已保存）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
