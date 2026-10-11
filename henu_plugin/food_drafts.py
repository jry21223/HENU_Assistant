"""Private durable drafts and atomic publication claims, scoped to a chat."""

from __future__ import annotations

import base64
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import math
import os
import re
from pathlib import Path
import secrets
import sqlite3
import time

from henu_plugin.kit_client import ENV_FILE

TTL = 600
CONFIRM = "确认发布"
CANCEL = "取消美食草稿"


def picture_receipt_question(text):
    # Recognize only receipt/visibility questions, never price or dish changes.
    normalized = re.sub(r"[\s？?！!。，,]", "", text)
    return (
        re.fullmatch(
            r"(?:你(?:这边)?|现在)?(?:能|能够|可以|有没有|是否)?"
            r"(?:收到|看到)(?:了)?(?:我发的|这张|刚才的)?"
            r"(?:图片|照片|图)(?:了)?(?:吗|么|没有)?"
            r"|(?:图片|照片|图)(?:你)?(?:收到|看到)(?:了)?(?:吗|么|没有)",
            normalized,
        )
        is not None
    )


CAMPUSES = {"minglun": "明伦", "jinming": "金明", "longzihu": "龙子湖"}
TIERS = {"hang": "夯", "top": "顶级", "elite": "人上人", "npc": "NPC", "bad": "拉完了"}


def chat_scope(secret, bot, sender, launcher_type, launcher_id):
    kind = getattr(launcher_type, "value", launcher_type)
    if kind not in {"person", "group"} or not launcher_id:
        raise ValueError("missing conversation")
    raw = json.dumps(
        [bot, sender, kind, str(launcher_id)], separators=(",", ":")
    ).encode()
    return hmac.new(
        secret.encode(), b"henu-food-draft-v1\n" + raw, hashlib.sha256
    ).hexdigest()


def inline_image(value):
    """Decode only inline bytes, never fetch a URL or read a caller path."""
    if not isinstance(value, str) or not value or len(value) > 2_800_000:
        raise ValueError("图片未提供可上传的数据，或超过单张 2MiB。")
    mime = ""
    if value.startswith("data:"):
        header, sep, value = value.partition(";base64,")
        if not sep:
            raise ValueError("图片格式不支持，请发送 JPG、PNG 或 WebP 图片。")
        mime = header[5:]
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("图片数据无效，请重新发送图片。") from exc
    actual = (
        "image/png"
        if raw.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if raw.startswith(b"\xff\xd8\xff")
        else "image/webp"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
        else ""
    )
    if not actual or (mime and mime != actual) or not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError("图片需为 JPG、PNG 或 WebP，单张不超过 2MiB。")
    return {"content_type": actual, "data": base64.b64encode(raw).decode()}


def validate_content(args):
    for key, maximum in {
        "venue_name": 160,
        "review_text": 2000,
        "price_reference": 200,
        "hours_reference": 200,
    }.items():
        if key in args and (not isinstance(args[key], str) or len(args[key]) > maximum):
            raise ValueError("店铺、锐评、价格或营业时间格式无效。")
    if (
        not args.get("venue_name", "").strip()
        or len(args.get("review_text", "").strip()) < 2
    ):
        raise ValueError("草稿需要店铺、校区、档位和至少 2 字的锐评。")
    if args.get("campus") not in CAMPUSES or args.get("tier") not in TIERS:
        raise ValueError("请选择有效的校区和档位。")
    dishes = args.get("dishes", [])
    if not isinstance(dishes, list) or len(dishes) > 6:
        raise ValueError("推荐菜品最多 6 道。")
    for dish in dishes:
        if (
            not isinstance(dish, dict)
            or set(dish) - {"name", "price", "reason"}
            or not isinstance(dish.get("name"), str)
            or not dish["name"].strip()
            or any(
                not isinstance(v, str)
                or len(v) > {"name": 80, "price": 40, "reason": 200}[k]
                for k, v in dish.items()
            )
        ):
            raise ValueError("推荐菜品格式无效。")


def preview(draft):
    args = draft["args"]
    lines = [
        "美食草稿（尚未发布）",
        "店铺：" + args["venue_name"],
        "校区：" + CAMPUSES[args["campus"]],
        "档位：" + TIERS[args["tier"]],
        "锐评：" + args["review_text"],
    ]
    for key, label in (("price_reference", "价格"), ("hours_reference", "营业时间")):
        if args.get(key):
            lines.append(label + "：" + args[key])
    for dish in args.get("dishes", []):
        lines.append("推荐菜：" + " · ".join(dish.values()))
    lines.append(f"图片：{len(draft['images'])} 张")
    if draft.get("image_error"):
        lines.append(
            draft["image_error"] + " 本草稿暂不能发布；可发送“取消美食草稿”后重新整理。"
        )
    else:
        lines.append(
            "可继续补充价格和图片；核对后单独发送“确认发布”。发布后公开，暂不支持修改。草稿 10 分钟内有效。"
        )
    return "\n".join(lines)


class FoodDrafts:
    def __init__(self):
        configured = os.environ.get("HENU_FOOD_DRAFT_DB")
        self.path = (
            Path(configured)
            if configured
            else ENV_FILE.parent / ".food-drafts" / "drafts.sqlite3"
        )
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)

    @contextmanager
    def _state(self, scope):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS drafts (scope TEXT PRIMARY KEY, updated REAL, state TEXT)"
            )
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM drafts WHERE updated < ?", (time.time() - 86400,))
            row = db.execute(
                "SELECT state FROM drafts WHERE scope = ?", (scope,)
            ).fetchone()
            state = json.loads(row[0]) if row else {}
            yield state
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?, ?, ?)",
                (scope, time.time(), json.dumps(state, ensure_ascii=False)),
            )

    @staticmethod
    def _active(state):
        if state.get("images_expires", 0) < time.time():
            state.pop("images", None)
            state.pop("image_error", None)
            state.pop("images_expires", None)
        draft = state.get("draft")
        if draft and draft["expires"] < time.time() and draft["status"] == "ready":
            state.pop("draft", None)
            return None
        return draft

    def receive(self, scope, source, stamp, query_id, text, images, image_error=""):
        """Record trusted ingress; return a reply or one durable publication claim."""
        now = time.time()
        if (
            not isinstance(source, str)
            or not source
            or len(source) > 256
            or isinstance(stamp, bool)
            or not isinstance(stamp, (int, float))
            or not math.isfinite(stamp)
            or not now - TTL <= stamp <= now + 30
        ):
            raise ValueError("无法验证消息来源或消息已过期，美食草稿尚未发布。")
        with self._state(scope) as state:
            previous = state.get("current", {})
            if source in state.get("seen", []) or stamp < previous.get("stamp", 0):
                return (
                    "这条消息已处理或已过期，未重复发布。"
                    if text in {CONFIRM, CANCEL}
                    else ""
                ), None
            state["seen"] = (state.get("seen", []) + [source])[-128:]
            current = {
                "source": source,
                "stamp": stamp,
                "query": query_id,
                "command": text if text in {CONFIRM, CANCEL} else "",
            }
            state["current"] = current
            draft = self._active(state)
            if text == CANCEL:
                if draft and draft["status"] != "ready":
                    return "发布已提交，不能取消；请查询“我的美食投稿”核验。", None
                state.pop("draft", None)
                state.pop("images", None)
                state.pop("image_error", None)
                return "美食草稿已取消，尚未发布。", None
            if text == CONFIRM and not images and not image_error:
                if not draft:
                    return "没有可发布的有效草稿，请先整理美食投稿并核对预览。", None
                if draft["status"] != "ready":
                    return draft.get(
                        "receipt"
                    ) or "发布结果尚不确定，请查询“我的美食投稿”，不会重复提交。", None
                if draft.get("image_error"):
                    return preview(draft), None
                if not draft.get("preview_delivered") or stamp <= draft.get(
                    "preview_at", 0
                ):
                    return (
                        "最新预览尚未送达，或确认早于预览，尚未发布；请重新整理并核对草稿。",
                        None,
                    )
                if draft.get("pending_source", "legacy-unverified") is not None:
                    return (
                        "还有投稿内容未整理进预览，尚未发布；请将补充内容发给我生成最新预览后再确认。",
                        None,
                    )
                draft["status"] = "submitting"
                arguments = draft["args"] | (
                    {"images": draft["images"]} if draft["images"] else {}
                )
                return "", (arguments, draft["nonce"])
            if draft and draft["status"] == "ready":
                if not images and not image_error and picture_receipt_question(text):
                    count = len(draft["images"])
                    answer = f"当前美食草稿已收到 {count} 张图片。"
                    if draft.get("image_error"):
                        answer += "\n" + draft["image_error"] + " 草稿暂不能发布。"
                    elif (
                        not draft.get("preview_delivered")
                        or draft.get("pending_source", "legacy-unverified") is not None
                    ):
                        answer += "\n还有投稿内容待整理或预览未送达，请先生成最新预览。"
                    else:
                        answer += (
                            "\n草稿内容未改变，核对已有预览后单独发送“确认发布”即可。"
                        )
                    return answer, None
                # Confirmation attempts are control messages, not content edits.
                draft["pending_source"] = source
            if draft and draft["status"] == "ready":
                target = draft
            else:
                target = state
            if images or image_error:
                if target is state:
                    state["images_expires"] = now + TTL
                target.setdefault("images", []).extend(images)
                if len(target["images"]) > 6:
                    target["images"] = target["images"][:6]
                    image_error = "图片超过 6 张。"
                if image_error:
                    target["image_error"] = image_error
                if (
                    draft
                    and draft["status"] == "ready"
                    and text in {"", "图片", "这个是图片", "照片"}
                ):
                    draft["preview_source"] = source
                    draft["preview_delivered"] = False
                    draft["expires"] = now + TTL
                    return preview(draft), None
            return "", None

    def prepare(self, scope, query_id, source, arguments):
        with self._state(scope) as state:
            current = state.get("current", {})
            if (
                not source
                or current.get("source") != source
                or current.get("query") != query_id
                or current.get("stamp", 0) < time.time() - TTL
                or current.get("command") in {CONFIRM, CANCEL}
            ):
                raise ValueError(
                    "无法验证当前内容消息，草稿尚未发布，请重新发送投稿内容。"
                )
            draft = self._active(state)
            if draft and draft["status"] == "submitting":
                raise ValueError(
                    "之前的发布结果尚不确定，请先查询“我的美食投稿”，不会重复提交。"
                )
            if not draft or draft["status"] != "ready":
                draft = {
                    "args": {},
                    "images": state.pop("images", []),
                    "image_error": state.pop("image_error", ""),
                    "nonce": secrets.token_urlsafe(24),
                    "status": "ready",
                }
            args = draft["args"] | arguments
            validate_content(args)
            draft.update(
                args=args,
                pending_source=source,
                preview_source=source,
                preview_delivered=False,
                expires=time.time() + TTL,
            )
            state["draft"] = draft
            return preview(draft)

    def delivered(self, scope, source, text):
        with self._state(scope) as state:
            draft = self._active(state)
            if (
                not draft
                or draft["status"] != "ready"
                or draft["preview_source"] != source
                or draft.get("pending_source", "legacy-unverified")
                not in {None, source}
                or preview(draft) != text
            ):
                raise ValueError("草稿已更新，请核对新的预览，尚未发布。")
            draft.update(
                preview_delivered=True, preview_at=time.time(), pending_source=None
            )

    def complete(self, scope, nonce, reply):
        with self._state(scope) as state:
            draft = state.get("draft", {})
            if draft.get("nonce") != nonce or draft.get("status") != "submitting":
                raise ValueError("publication claim changed")
            draft.update(status="finished", receipt=reply, images=[])
