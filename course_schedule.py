import argparse
import base64
import datetime as dt
import getpass
import json
import math
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from schedule_cleaner import clean_schedule_grid_file
from secure_storage import (
    load_encrypted_profile,
    save_encrypted_profile,
    decrypt_value,
)


BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "henu_cookies.json"
PROFILE_FILE = BASE_DIR / "henu_profile.json"
DEFAULT_HOME_URL = "https://xk.henu.edu.cn/frame/homes.action?v=07364432695342088912561"
OUTPUT_DIR = BASE_DIR / "output"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class HenuXkClient:
    AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
    PERSIST_COOKIE_NAMES = {"CASTGC", "happyVoyage", "platformMultilingual"}

    def __init__(self, username: str, password: str, saved_cookies: dict[str, Any] | None = None):
        self.username = str(username).strip()
        self.password = password or ""
        self.base_url = "https://xk.henu.edu.cn"
        self.cas_login_url = "https://ids.henu.edu.cn/authserver/login"

        self.session = requests.Session()
        # 避免本地代理影响 http 跳转链路
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

        if saved_cookies:
            self.session.cookies.update(saved_cookies)

    def get_cookies(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for cookie in self.session.cookies:
            if cookie.name in self.PERSIST_COOKIE_NAMES:
                result[cookie.name] = cookie.value
        return result

    @staticmethod
    def _decode_text(resp: requests.Response) -> str:
        content = resp.content or b""
        for encoding in ("utf-8", "gbk", "gb2312"):
            try:
                return content.decode(encoding)
            except Exception:
                continue
        return content.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_title(text: str) -> str:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        return re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    @staticmethod
    def _is_auth_invalid_page(text: str) -> bool:
        compact = re.sub(r"\s+", "", text).lower()
        markers = [
            "window.top.location.href='/'",
            'window.top.location.href="/"',
            "凭证已失效",
            "请重新登录",
        ]
        return any(marker in compact for marker in markers)

    @staticmethod
    def _is_invalid_request_page(final_url: str, text: str, title: str) -> bool:
        url_lower = str(final_url).lower()
        title_lower = str(title).lower()
        if "frame/errors/405.jsp" in url_lower:
            return True
        if "无效访问请求" in text or "错误:无效访问请求" in text:
            return True
        if "閿欒:鏃犳晥璁块棶璇锋眰" in text:
            return True
        if "invalid" in title_lower and "request" in title_lower:
            return True
        return False

    def _random_string(self, length: int) -> str:
        return "".join(
            self.AES_CHARS[math.floor(random.random() * len(self.AES_CHARS))]
            for _ in range(length)
        )

    def _encrypt_password(self, password: str, salt: str) -> str:
        random_prefix = self._random_string(64)
        iv_str = self._random_string(16)
        text = random_prefix + password
        key_bytes = salt.encode("utf-8")
        iv_bytes = iv_str.encode("utf-8")
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))).decode("utf-8")

    def _cas_auth_url(self) -> str:
        service_url = f"{self.base_url}/caslogin"
        return f"{self.cas_login_url}?service={service_url}"

    def fetch_page(self, url: str, referer: str | None = None) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer

        resp = self.session.get(url, headers=headers or None, allow_redirects=True, timeout=30)
        text = self._decode_text(resp)
        title = self._extract_title(text)

        return {
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "title": title,
            "invalid_auth": self._is_auth_invalid_page(text),
            "invalid_request": self._is_invalid_request_page(resp.url, text, title),
            "text": text,
        }

    @staticmethod
    def _extract_var(text: str, name: str) -> str:
        pattern = rf"var\s+{re.escape(name)}\s*=\s*'(.*?)';"
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ""

    def fetch_user_context(self) -> dict[str, Any]:
        url = f"{self.base_url}/frame/home/js/SetMainInfo.jsp?v={int(dt.datetime.now().timestamp())}"
        result = self.fetch_page(url)
        text = result["text"]

        login_id = self._extract_var(text, "_loginid") or self._extract_var(text, "G_LOGIN_ID")
        user_code = self._extract_var(text, "_userCode") or self._extract_var(text, "G_USER_CODE")
        user_type = self._extract_var(text, "_usertype") or self._extract_var(text, "G_USER_TYPE")
        current_xn = self._extract_var(text, "_currentXn")
        current_xq = self._extract_var(text, "_currentXq")
        school_code = self._extract_var(text, "_schoolCode") or self._extract_var(text, "G_SCHOOL_CODE")

        is_guest = login_id.lower() in {"", "guest", "kingo.guest"}
        authenticated = bool(login_id) and not is_guest and not result["invalid_auth"]

        return {
            "authenticated": authenticated,
            "login_id": login_id,
            "user_code": user_code,
            "user_type": user_type,
            "current_xn": current_xn,
            "current_xq": current_xq,
            "school_code": school_code,
            "source_url": result["final_url"],
            "source_file_content": text,
        }

    def _check_logged_in(self) -> bool:
        context = self.fetch_user_context()
        return bool(context.get("authenticated"))

    def _clear_runtime_cookies(self) -> None:
        self.session.cookies.clear()

    def login(self) -> bool:
        if self._check_logged_in():
            return True

        cas_auth_url = self._cas_auth_url()

        # 1) 先试已有 TGT 自动跳转
        try:
            self.session.get(cas_auth_url, allow_redirects=True, timeout=30)
            if self._check_logged_in():
                return True
        except Exception:
            pass

        if not self.password:
            return False

        # 2) 密码登录 CAS（同图书馆流程）
        try:
            self._clear_runtime_cookies()
            login_page = self.session.get(cas_auth_url, timeout=30)
            login_text = self._decode_text(login_page)
            execution_match = re.search(r'name="execution" value="(.*?)"', login_text)
            salt_match = re.search(r'id="pwdEncryptSalt" value="(.*?)"', login_text)
            if not execution_match or not salt_match:
                return False

            form_data = {
                "username": self.username,
                "password": self._encrypt_password(self.password, salt_match.group(1)),
                "captcha": "",
                "_eventId": "submit",
                "cllt": "userNameLogin",
                "dllt": "generalLogin",
                "lt": "",
                "execution": execution_match.group(1),
            }
            self.session.post(login_page.url, data=form_data, allow_redirects=True, timeout=35)
            return self._check_logged_in()
        except Exception:
            return False

    def discover_schedule_urls(self, home_html: str, user_type: str = "") -> list[str]:
        found: list[str] = []

        if user_type == "STU":
            found.append("/student/xkjg.wdkb.jsp")
        else:
            found.extend(["/wjstgdfw/jxap.jxapb.html", "/frame/desk/showLessonSchedule4User.action"])

        for match in re.findall(r"""["'](/[^"'<>]*(?:kbcx|xskb|kb|timetable)[^"'<>]*)["']""", home_html, re.I):
            lower = match.lower()
            if any(lower.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg")):
                continue
            found.append(match)

        found.extend(
            [
                "/xskbcx/xskbcx_cxXsKb.html",
                "/xskbcx/xskbcx_cxXsKb.html?gnmkdm=N253508",
                "/xskbcx/xskbcx_cxXsKb.html?gnmkdm=N253508&layout=default",
                "/xskbcx/xskbcx_cxXsgrkb.html",
                "/xskbcx/xskbcx_cxXsgrkb.html?gnmkdm=N253507",
                "/xskbcx/xskbcx_cxXsKbxxIndex.html",
            ]
        )

        deduped: list[str] = []
        seen: set[str] = set()
        for item in found:
            abs_url = urljoin(f"{self.base_url}/", item)
            if abs_url not in seen:
                seen.add(abs_url)
                deduped.append(abs_url)
        return deduped

    @staticmethod
    def extract_text_preview(html: str, limit_lines: int = 40) -> str:
        body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        body = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", body)
        body = re.sub(r"(?is)<[^>]+>", " ", body)
        body = re.sub(r"[ \t]+", " ", body)
        lines = [line.strip() for line in body.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines[:limit_lines])

    @staticmethod
    def _extract_student_xh(schedule_page_html: str, fallback: str) -> str:
        patterns = [
            r'id=["\']xh["\'][^>]*value=["\'](.*?)["\']',
            r'name=["\']xh["\'][^>]*value=["\'](.*?)["\']',
            r'value=["\'](.*?)["\'][^>]*id=["\']xh["\']',
            r'value=["\'](.*?)["\'][^>]*name=["\']xh["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, schedule_page_html, re.I)
            if match and match.group(1).strip():
                return match.group(1).strip()
        return fallback

    @staticmethod
    def _extract_student_term(schedule_page_html: str) -> tuple[str, str]:
        def _pick(field: str) -> str:
            patterns = [
                rf'id=["\']{field}["\'][^>]*value=["\'](.*?)["\']',
                rf'name=["\']{field}["\'][^>]*value=["\'](.*?)["\']',
                rf'value=["\'](.*?)["\'][^>]*id=["\']{field}["\']',
                rf'value=["\'](.*?)["\'][^>]*name=["\']{field}["\']',
            ]
            for pattern in patterns:
                m = re.search(pattern, schedule_page_html, re.I)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            return ""

        xn = _pick("xn")
        xq = _pick("xq") or _pick("xq_m")
        return xn, xq

    @staticmethod
    def _extract_student_data_paths(schedule_page_html: str) -> tuple[str, str]:
        default_list = "../wsxk/xkjg.ckdgxsxdkchj_data10319.jsp"
        default_grid = "../student/wsxk.xskcb10319.jsp"
        patterns = [
            r"""frmaction\s*=\s*\$\(["']cxfs_lb["']\)\.checked\s*\?\s*["']([^"']+)["']\s*:\s*["']([^"']+)["']""",
            r"""frmaction\s*=\s*[^;]*\?\s*["']([^"']+)["']\s*:\s*["']([^"']+)["']""",
        ]
        for pattern in patterns:
            m = re.search(pattern, schedule_page_html, re.I)
            if m:
                return m.group(1), m.group(2)

        jsp_paths = re.findall(r"""["']([^"']+\.jsp)["']""", schedule_page_html, re.I)
        list_path = ""
        grid_path = ""
        for path in jsp_paths:
            lower = path.lower()
            if not list_path and ("ckdgxsxdkchj_data" in lower or ("xkjg" in lower and "data" in lower)):
                list_path = path
            if (
                not grid_path
                and "xskcb" in lower
                and "excel" not in lower
                and "_exp" not in lower
            ):
                grid_path = path
            if list_path and grid_path:
                break

        return list_path or default_list, grid_path or default_grid

    def build_student_schedule_data_urls(
        self,
        schedule_page_url: str,
        schedule_page_html: str,
        xn: str,
        xq: str,
        xh: str,
    ) -> list[tuple[str, str]]:
        list_path, grid_path = self._extract_student_data_paths(schedule_page_html)
        raw_params = f"xn={xn}&xq={xq}&xh={xh}"
        encoded_params = base64.b64encode(raw_params.encode("utf-8")).decode("ascii")

        list_url = urljoin(schedule_page_url, list_path)
        grid_url = urljoin(schedule_page_url, grid_path)
        plain_params = [raw_params, f"xnm={xn}&xqm={xq}&xh={xh}"]

        candidates: list[tuple[str, str]] = [
            ("list", f"{list_url}?params={encoded_params}"),
            ("grid", f"{grid_url}?params={encoded_params}"),
        ]
        for query in plain_params:
            candidates.append(("list", f"{list_url}?{query}"))
            candidates.append(("grid", f"{grid_url}?{query}"))

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, url in candidates:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((label, url))
        return deduped

    def build_direct_student_schedule_data_urls(
        self,
        xn: str,
        xq: str,
        xh: str,
    ) -> list[tuple[str, str]]:
        raw_params = f"xn={xn}&xq={xq}&xh={xh}"
        encoded_params = base64.b64encode(raw_params.encode("utf-8")).decode("ascii")
        plain_params = [raw_params, f"xnm={xn}&xqm={xq}&xh={xh}"]

        list_paths = [
            "/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp",
            "/wsxk/xkjg.ckdgxsxdkchj_data.jsp",
        ]
        grid_paths = [
            "/student/wsxk.xskcb10319.jsp",
            "/student/wsxk.xskcb.jsp",
        ]

        candidates: list[tuple[str, str]] = []
        for path in list_paths:
            abs_url = urljoin(f"{self.base_url}/", path)
            candidates.append(("list", f"{abs_url}?params={encoded_params}"))
            for query in plain_params:
                candidates.append(("list", f"{abs_url}?{query}"))

        for path in grid_paths:
            abs_url = urljoin(f"{self.base_url}/", path)
            candidates.append(("grid", f"{abs_url}?params={encoded_params}"))
            for query in plain_params:
                candidates.append(("grid", f"{abs_url}?{query}"))

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, url in candidates:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((label, url))
        return deduped

def prompt_text(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def prompt_password(default_exists: bool) -> str:
    if default_exists:
        value = getpass.getpass("密码 [留空沿用已保存密码]: ").strip()
    else:
        value = getpass.getpass("密码: ").strip()
    return value


def _save_output_file(stem: str, timestamp: str, text: str, ext: str = "html") -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{stem}_{timestamp}.{ext}"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _is_useful_schedule_page(page: dict[str, Any]) -> bool:
    if page.get("invalid_auth") or page.get("invalid_request"):
        return False

    text = str(page.get("text", ""))
    title_lower = str(page.get("title", "")).lower()

    if any(keyword in title_lower for keyword in ("课表", "排课", "timetable", "kb", "课程表")):
        return True
    if any(keyword in text for keyword in ("课表", "课程", "星期", "周一", "周二", "学年学期")):
        return True
    if re.search(r"\d+\s*-\s*\d+周\s*[一二三四五六日天]\[\d", text):
        return True
    if "id='mytable'" in text or 'id="mytable"' in text:
        return True
    return False


def run_fetch(
    student_id: str,
    password: str,
    home_url: str,
    schedule_url: str | None = None,
    xn: str | None = None,
    xq: str | None = None,
) -> dict[str, Any]:
    cookies = load_json(COOKIE_FILE)
    client = HenuXkClient(student_id, password, cookies or None)

    if not client.login():
        return {"success": False, "msg": "登录失败，请检查账号密码或 CAS 状态"}

    context = client.fetch_user_context()
    if not context.get("authenticated"):
        return {
            "success": False,
            "msg": "已进入系统但仍为游客态(kingo.guest)，无法查看个人课表",
            "login_id": context.get("login_id", ""),
            "user_type": context.get("user_type", ""),
        }

    save_json(COOKIE_FILE, client.get_cookies())

    home_result = client.fetch_page(home_url)
    if home_result["invalid_auth"]:
        return {"success": False, "msg": "登录后访问首页仍提示未登录，可能会话失效"}

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    home_file = _save_output_file("home", timestamp, home_result["text"], "html")
    main_info_file = _save_output_file("set_main_info", timestamp, context["source_file_content"], "js")

    tried: list[dict[str, Any]] = []
    generated_files: dict[str, str] = {
        "home_file": home_file,
        "main_info_file": main_info_file,
    }

    def _record(page: dict[str, Any], label: str) -> None:
        tried.append(
            {
                "label": label,
                "url": page["url"],
                "final_url": page["final_url"],
                "status_code": page["status_code"],
                "title": page["title"],
                "invalid_auth": page["invalid_auth"],
                "invalid_request": page["invalid_request"],
            }
        )

    chosen: dict[str, Any] | None = None

    if schedule_url:
        page = client.fetch_page(schedule_url, referer=home_result["final_url"])
        _record(page, "manual_schedule_url")
        if _is_useful_schedule_page(page):
            chosen = page
    else:
        user_type = str(context.get("user_type", "") or "").upper()

        if user_type == "STU":
            target_xn = str(xn or context.get("current_xn") or "").strip()
            target_xq = str(xq or context.get("current_xq") or "").strip()
            target_xh = str(
                context.get("user_code")
                or context.get("login_id")
                or student_id
                or ""
            ).strip()
            data_urls: list[tuple[str, str]] = []

            schedule_entry = client.fetch_page(
                urljoin(f"{client.base_url}/", "student/xkjg.wdkb.jsp"),
                referer=home_result["final_url"],
            )
            _record(schedule_entry, "student_schedule_entry")

            if not schedule_entry["invalid_auth"] and not schedule_entry["invalid_request"]:
                entry_file = _save_output_file("schedule_entry", timestamp, schedule_entry["text"], "html")
                generated_files["schedule_entry_file"] = entry_file

                entry_xn, entry_xq = client._extract_student_term(schedule_entry["text"])
                if not target_xn and entry_xn:
                    target_xn = str(entry_xn).strip()
                if not target_xq and entry_xq:
                    target_xq = str(entry_xq).strip()
                target_xh = client._extract_student_xh(schedule_entry["text"], target_xh)

                if target_xn and target_xq and target_xh:
                    data_urls.extend(
                        client.build_student_schedule_data_urls(
                            schedule_page_url=schedule_entry["final_url"],
                            schedule_page_html=schedule_entry["text"],
                            xn=target_xn,
                            xq=target_xq,
                            xh=target_xh,
                        )
                    )

            if target_xn and target_xq and target_xh:
                data_urls.extend(
                    client.build_direct_student_schedule_data_urls(
                        xn=target_xn,
                        xq=target_xq,
                        xh=target_xh,
                    )
                )

            if data_urls:
                deduped_urls: list[tuple[str, str]] = []
                seen_urls: set[str] = set()
                for label, data_url in data_urls:
                    if data_url in seen_urls:
                        continue
                    seen_urls.add(data_url)
                    deduped_urls.append((label, data_url))

                student_data_pages: dict[str, dict[str, Any]] = {}
                # 优先尝试新版列表接口，失败后再尝试二维表
                deduped_urls = sorted(deduped_urls, key=lambda item: 0 if item[0] == "list" else 1)

                referer_url = schedule_entry["final_url"] if schedule_entry else home_result["final_url"]
                for label, data_url in deduped_urls:
                    page = client.fetch_page(data_url, referer=referer_url)
                    _record(page, f"student_schedule_data_{label}")
                    student_data_pages[label] = page

                    if _is_useful_schedule_page(page):
                        chosen = page
                        generated_files[f"schedule_{label}_file"] = _save_output_file(
                            f"schedule_{label}", timestamp, page["text"], "html"
                        )
                        break

                if chosen is None:
                    for label in ("list", "grid"):
                        page = student_data_pages.get(label)
                        if page and not page["invalid_auth"] and not page["invalid_request"]:
                            chosen = page
                            generated_files[f"schedule_{label}_file"] = _save_output_file(
                                f"schedule_{label}", timestamp, page["text"], "html"
                            )
                            break

        if chosen is None:
            urls = client.discover_schedule_urls(home_result["text"], user_type=str(context.get("user_type", "")))
            for url in urls:
                page = client.fetch_page(url, referer=home_result["final_url"])
                _record(page, "fallback_discovery")
                if _is_useful_schedule_page(page):
                    chosen = page
                    break

    preview_file = ""
    schedule_file = ""
    clean_schedule_md = ""
    clean_schedule_json = ""
    clean_schedule_error = ""

    if chosen:
        schedule_file = _save_output_file("schedule", timestamp, chosen["text"], "html")
        preview_file = _save_output_file(
            "schedule_preview",
            timestamp,
            HenuXkClient.extract_text_preview(chosen["text"]),
            "txt",
        )

        # 若抓到了二维课表页面，自动生成结构化 JSON 与 Markdown
        clean_source = generated_files.get("schedule_grid_file") or schedule_file
        try:
            cleaned = clean_schedule_grid_file(clean_source, OUTPUT_DIR)
            clean_schedule_json = cleaned["files"].get("clean_schedule_json", "")
            clean_schedule_md = cleaned["files"].get("clean_schedule_md", "")
            generated_files.update(cleaned["files"])
        except Exception as exc:
            clean_schedule_error = str(exc)

    return {
        "success": bool(chosen),
        "msg": "已抓取课表页面" if chosen else "已登录，但未自动识别到课表页面，请手动指定 --schedule-url",
        "login_id": context.get("login_id", ""),
        "user_type": context.get("user_type", ""),
        "target_term": {
            "xn": str(xn or context.get("current_xn") or ""),
            "xq": str(xq or context.get("current_xq") or ""),
        },
        "home_url": home_result["final_url"],
        "schedule_url": chosen["final_url"] if chosen else "",
        "schedule_title": chosen["title"] if chosen else "",
        "schedule_file": schedule_file,
        "preview_file": preview_file,
        "clean_schedule_file_md": clean_schedule_md,
        "clean_schedule_file_json": clean_schedule_json,
        "clean_schedule_error": clean_schedule_error,
        "generated_files": generated_files,
        "tried_urls": tried,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HENU 课表查看（CAS 登录）")
    parser.add_argument(
        "quick",
        nargs="?",
        choices=["setup", "show", "fetch"],
        help="快捷命令：setup(配置) show(查看配置) fetch(抓取课表)",
    )
    parser.add_argument("-u", "--student-id", help="学号（不传则读取已保存配置）")
    parser.add_argument("-p", "--password", help="密码（不传则读取已保存配置）")
    parser.add_argument("--setup", action="store_true", help="交互式配置账号并保存")
    parser.add_argument("--show", action="store_true", help="显示当前配置")
    parser.add_argument("--fetch", action="store_true", help="执行一次登录并抓取课表")
    parser.add_argument("--home-url", default=DEFAULT_HOME_URL, help="系统首页地址")
    parser.add_argument("--schedule-url", default=None, help="指定课表页面地址（可选）")
    parser.add_argument("--xn", default=None, help="指定学年，如 2025")
    parser.add_argument("--xq", default=None, help="指定学期，如 1/2")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.quick == "setup":
        args.setup = True
    if args.quick == "show":
        args.show = True
    if args.quick == "fetch":
        args.fetch = True

    # 使用加密存储加载配置
    profile = load_encrypted_profile(PROFILE_FILE)

    if args.setup:
        profile["student_id"] = prompt_text("学号", str(profile.get("student_id", "") or ""))
        pwd_input = prompt_password(default_exists=bool(profile.get("password")))
        profile["password"] = pwd_input or str(profile.get("password", "") or "")
        save_encrypted_profile(PROFILE_FILE, profile)
        print(f"配置已保存: {PROFILE_FILE}")

    if args.show:
        print("当前配置:")
        print(f"  学号: {profile.get('student_id', '')}")
        print("  密码: " + ("已保存" if profile.get("password") else "未保存"))
        print(f"  来源(本地): {PROFILE_FILE}")

    if not (args.fetch or args.setup or args.show):
        args.fetch = True

    if args.fetch:
        student_id = args.student_id or str(profile.get("student_id", "") or "")
        password = args.password or str(profile.get("password", "") or "")

        if not student_id:
            print(
                json.dumps(
                    {"success": False, "msg": "缺少学号，请先运行: python3 course_schedule.py setup"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)

        if not password:
            print(
                json.dumps(
                    {"success": False, "msg": "缺少密码，请先运行: python3 course_schedule.py setup"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)

        result = run_fetch(
            student_id=student_id,
            password=password,
            home_url=args.home_url,
            schedule_url=args.schedule_url,
            xn=args.xn,
            xq=args.xq,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("success"):
            raise SystemExit(1)
