"""Bot reservation regression: only the external school's HTTP is replaced."""

import asyncio
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
from components.cli_tools.henu_cli_safe import HenuCliSafe
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_mcp.core.secure_storage import encrypt_value
from campus_core import seat_reservation, auth

DAY = "2026-11-16"
COMMAND = "library reserve --location '金明校区-金明二楼-208 过刊阅览室' --seat-no 068 --date 2026-11-16 --time 08:00 --end-time 22:00"


class MemoryPlugin:
    def __init__(self):
        self.storage = {
            "user:booking-fixture:profile": json.dumps(
                {
                    "student_id": "fixture-student",
                    "password": encrypt_value("fixture-password"),
                }
            ).encode(),
            "user:booking-fixture:library_cookie": json.dumps(
                {"_v4_token": "fixture-token", "_subject_student_id": "fixture-student"}
            ).encode(),
        }
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])

    async def get_plugin_storage_keys(self):
        return list(self.storage)

    async def get_plugin_storage(self, key):
        return self.storage.get(key, b"{}")

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


@pytest.mark.parametrize(
    "available,readback", [(True, True), (True, False), (False, False)]
)
def test_bot_booking_preview_confirm_and_readback(monkeypatch, available, readback):
    clock = lambda *a, **k: dt.datetime(
        2026, 11, 16, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))
    )
    monkeypatch.setattr(seat_reservation, "_now_dt", clock)
    monkeypatch.setattr(auth, "_now_dt", clock)
    calls, submitted = [], []

    def request(self, method, url, **kw):
        path = urlsplit(url).path
        if urlsplit(url).netloc == "ids.henu.edu.cn":
            response = requests.Response()
            response.status_code = 200
            response.url = (
                "https://zwyy.henu.edu.cn/h5/index.html#/cas/?cas=fixture-cas"
            )
            response._content = b""
            return response
        assert urlsplit(url).netloc == "zwyy.henu.edu.cn"
        calls.append(path)
        if path == "/v4/login/user":
            data = {"member": {"token": "fixture-token"}}
        elif path == "/v4/space/pick":
            data = {"area": [{"id": "13", "name": "金明校区-金明二楼-208 过刊阅览室"}]}
        elif path == "/v4/Space/map":
            data = {
                "type": "1",
                "name": "金明校区-金明二楼-208 过刊阅览室",
                "date": {
                    "reserveType": "1",
                    "list": [
                        {
                            "day": DAY,
                            "book_start": "06:30",
                            "book_end": "23:00",
                            "times": [
                                {
                                    "id": "school-segment",
                                    "status": 1,
                                    "start": "08:00",
                                    "end": "22:00",
                                }
                            ],
                        }
                    ],
                },
            }
        elif path == "/v4/Space/seat":
            data = {
                "list": [
                    {
                        "id": str(3605 + i),
                        "no": f"{i:03}",
                        "status": 1 if available or i != 68 else 2,
                    }
                    for i in range(1, 137)
                ]
            }
        elif path == "/v4/space/confirm":
            encrypted = kw["json"]["aesjson"]
            # Decode independently using the official daily key contract.
            import base64

            payload = json.loads(
                unpad(
                    AES.new(
                        b"2026111661116202", AES.MODE_CBC, b"ZZWBKJ_ZHIHUAWEI"
                    ).decrypt(base64.b64decode(encrypted)),
                    16,
                )
            )
            assert (
                payload["seat_id"] == "3673" and payload["segment"] == "school-segment"
            )
            submitted.append(payload)
            data = {}
        elif path == "/v4/index/subscribe":
            data = (
                [
                    {
                        "id": "fixture-record",
                        "type": 1,
                        "roomName": "208 过刊阅览室",
                        "seatName": "068",
                        "no": "068",
                        "showTime": f"{DAY} 08:00:00～22:00:00",
                        "status": 2,
                    }
                ]
                if submitted and readback
                else []
            )
        elif path == "/v4/member/info":
            data = {}
        else:
            raise AssertionError(path)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(
            {"code": 0, "msg": "操作成功", "data": data}
        ).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)

    async def run():
        plugin = MemoryPlugin()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="booking-fixture",
            launcher_id="booking-fixture",
            launcher_type=LauncherTypes.PERSON,
        )
        preview = await tool.call({"command": COMMAND}, session, 1)
        assert not submitted
        if not available:
            assert preview.get("error_code") == "seat_unavailable", preview
            return
        assert preview.get("confirmation_required") is True, preview
        assert "068" in preview["reply_text"] and "22:00" in preview["reply_text"]
        pending = json.loads(plugin.storage["user:booking-fixture:pending_operation"])
        final = await tool.call({"command": "confirm " + pending["token"]}, session, 2)
        assert final.get("success") is readback, final
        assert final.get("submit_success") is True, final
        if readback:
            assert final["verification"]["verified"] is True
            assert final["verification"]["record"]["seatName"] == "068"
        else:
            assert final.get("retryable") is False, final
        assert len(submitted) == 1
        assert "/v4/index/subscribe" in calls
        again = await tool.call({"command": "confirm " + pending["token"]}, session, 3)
        assert len(submitted) == 1

    asyncio.run(run())
