"""为已导入的电费项目补齐登录态续期信息。先用 --import-curl 保存项目。

密码由 WMXY_PASSWORD 环境变量提供。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from campus_core.atomic_io import atomic_write_json, file_transaction  # noqa: E402
from campus_core.electricity_client import (  # noqa: E402
    ElectricityError, _exchange_cookie, _load_context, validate_context,
)
from campus_core.wmxy_auth import AuthError, exchange_secret, login_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True, help="已通过 --import-curl 导入的私有配置")
    parser.add_argument("--device", type=Path, required=True, help="含 id/model/product/release 的私有 JSON")
    args = parser.parse_args()
    account = os.environ.get("WMXY_ACCOUNT", "").strip()
    password = os.environ.get("WMXY_PASSWORD", "")
    if not account or not password:
        print("需要 WMXY_ACCOUNT 和 WMXY_PASSWORD 环境变量。", file=sys.stderr)
        return 1

    try:
        with file_transaction(args.context):
            try:
                context = _load_context(args.context)
                validate_context(context)
            except ElectricityError:
                print("请先用 --import-curl 导入有效的电费项目配置。", file=sys.stderr)
                return 1
            device_info = json.loads(args.device.read_text(encoding="utf-8"))
            device = {"deviceId": device_info["id"],
                      "telephoneModel": device_info.get("model") or "sdk_gphone_arm64",
                      "shebeixinghao": device_info.get("product") or "sdk_gphone_arm64",
                      "telephoneInfo": device_info.get("release") or "11"}
            with httpx.Client(timeout=20, trust_env=False) as http:
                session = exchange_secret(http)
                user = login_password(http, account=account, password=password, session=session, device=device)
            if not session.session or not user.get("token"):
                raise AuthError("登录响应缺少续期信息。")

            # 服务端可能已轮换 Token。先保存新续期信息，并保留原有云缴费 Cookie。
            context = {
                **context, "session_id": session.session,
                "custom_id": str(user.get("customId") or context.get("custom_id") or ""),
                "login_token": str(user["token"]), "user_id": str(user.get("id") or ""),
                "login_name": str(user.get("mobile") or account), "device_id": device["deviceId"],
                "device_model": device["telephoneModel"], "device_product": device["shebeixinghao"],
                "device_release": device["telephoneInfo"],
            }
            atomic_write_json(args.context, context)
            if not context["user_id"]:
                print("已保存新 Token，登录响应缺少用户编号，请重新核对续期配置。", file=sys.stderr)
                return 1
            try:
                with httpx.Client(timeout=20, trust_env=False) as http:
                    report = _exchange_cookie(http, context, session.session)
            except ElectricityError as exc:
                print(f"续期信息已保存，换票失败：{exc}", file=sys.stderr)
                return 1
            if not report["recognized"]:
                print("续期信息已保存，云缴费会话未通过选房检查。", file=sys.stderr)
                return 1
            atomic_write_json(args.context, {**context, "cookie": report["cookie"]})
    except (AuthError, ValueError, KeyError, TypeError, OSError):
        print("登录或写入续期配置失败，请核对账号、设备与文件权限。", file=sys.stderr)
        return 1

    print("续期信息已保存，云缴费会话已通过选房检查。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
