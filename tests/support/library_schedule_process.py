"""Real process/reopen fixture for approved persistent worker seam."""

import asyncio
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

import requests
import mcp_server

from henu_mcp.core.secure_storage import encrypt_value
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.library_schedule import LibraryScheduler

root = Path(sys.argv[1])
stage = sys.argv[2]
root.mkdir(parents=True, exist_ok=True)
for name, value in {
    "CORE_URL": "https://core.test.invalid",
    "PORTAL_URL": "https://portal.test.invalid",
    "CLIENT_ID": "test-bot",
    "KEY_ID": "test-key",
    "SECRET": "s" * 32,
    "BOT_UUID": "bot",
}.items():
    os.environ["HENU_KIT_" + name] = value


class Response:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, _):
        yield json.dumps(
            {
                "data": {
                    "bound": True,
                    "plan": "lifetime",
                    "lifetime": True,
                    "allowed": True,
                    "benefits": ["yuketang", "ucampus", "chaoxing", "library_schedule"],
                    "checked_at": dt.datetime.now(dt.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
            }
        ).encode()


requests.post = lambda *a, **kw: Response()


class FilePlugin:
    def __init__(self):
        self.service = HardenedHenuPluginService(root)

    def path(self, key):
        return root / hashlib.sha256(key.encode()).hexdigest()

    async def get_plugin_storage(self, key):
        path = self.path(key)
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()

    async def set_plugin_storage(self, key, raw):
        path = self.path(key)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        os.replace(temp, path)


def school(**params):
    with (root / "school-calls").open("a") as out:
        out.write(json.dumps(params) + "\n")
    if stage == "crash":
        os._exit(77)
    return {"success": True, "msg": "synthetic receipt"}


mcp_server.library_reserve = school


async def run():
    plugin = FilePlugin()
    if stage == "create":
        await plugin.set_plugin_storage(
            "user:alice:profile",
            json.dumps(
                {
                    "student_id": "synthetic-student",
                    "password": encrypt_value("SyntheticPassword!"),
                }
            ).encode(),
        )
    clock = dt.datetime.fromisoformat(
        "2026-10-10T06:59:00+08:00"
        if stage == "create"
        else "2026-10-10T07:00:00+08:00"
    )
    scheduler = LibraryScheduler(plugin, clock=lambda: clock)
    await scheduler.start()
    conversation = {"launcher_type": "person", "launcher_id": "alice"}
    if stage == "create":
        command = "library schedule add --run-at 2026-10-10T07:00:00+08:00 --location 金明馆北区 --seat-no 201 --date 2026-10-11 --time 08:00 --end-time 10:00"
        await scheduler.execute(command, "alice", conversation, 1)
        pending = await scheduler.valid_pending("alice", conversation, 2)
        accepted = await scheduler.execute(
            "library schedule confirm",
            "alice",
            conversation,
            2,
            confirmation_token=pending["token"],
        )
        assert accepted["status"] == "pending", accepted
    else:
        await scheduler.run_due()
    listing = await scheduler.execute("library schedule list", "alice", conversation, 3)
    await scheduler.shutdown()
    print(json.dumps(listing, ensure_ascii=False))


asyncio.run(run())
