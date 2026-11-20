"""用合成响应检查云缴费的只读契约和错误边界。"""

import io
import json
import sys
import traceback
from decimal import Decimal

import httpx
import pytest

from campus_core.electricity_client import (
    ORIGIN,
    ROOM_ACCESS,
    ROOM_CATALOG,
    ROOM_FIELDS,
    ROOM_STATE,
    ElectricityAuthError,
    ElectricityInputError,
    ElectricityNetworkError,
    ElectricityProtocolError,
    main,
    query_balance,
)

PRIVATE = "synthetic-private-value"


def check(respond, **overrides):
    def handle(request):
        assert request.method == "GET"
        assert str(request.url).split("?", 1)[0] == ROOM_STATE
        assert set(request.url.params.keys()) == ROOM_FIELDS
        return respond(request)

    config = {
        "room_ref": dict.fromkeys(ROOM_FIELDS, "synthetic"),
        "cookie": f"TEST={PRIVATE}",
        "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
    }
    config.update(overrides)
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        return query_balance(http, **config)


def success(quantity, unit="度"):
    return lambda _: httpx.Response(200, json={"returncode": "100", "quantity": quantity, "quantityunit": unit})


@pytest.mark.parametrize(
    ("quantity", "expected"), [("12.35", Decimal("12.35")), ("0", Decimal("0")), (None, None), ("-1", None)],
)
def test_query_balance_parses_quantity(quantity, expected):
    reading = check(success(quantity))
    assert reading.quantity == expected
    assert reading.unit == "度"
    assert reading.currency_balance is None


def test_query_balance_uses_source_unit():
    assert check(success("1.25", "kWh")).unit == "kWh"


def timeout(request):
    raise httpx.ReadTimeout(PRIVATE, request=request)


def disconnect(request):
    raise httpx.ConnectError(PRIVATE, request=request)


def no_request(_):
    raise AssertionError("无效输入不能发送请求")


@pytest.mark.parametrize(
    ("respond", "error_type", "expected", "overrides"),
    (
        (no_request, ElectricityInputError, "房间参数", {"room_ref": {}}),
        (no_request, ElectricityInputError, "房间参数", {
            "room_ref": {**dict.fromkeys(ROOM_FIELDS, "synthetic"), "roomverify": ""},
        }),
        (no_request, ElectricityInputError, "房间参数", {
            "room_ref": {**dict.fromkeys(ROOM_FIELDS, "synthetic"), "payProId": "  "},
        }),
        (no_request, ElectricityInputError, "缺少有效", {"cookie": f" TEST={PRIVATE}"}),
        (no_request, ElectricityInputError, "地址不匹配", {"referer": "https://example.invalid"}),
        (lambda _: httpx.Response(302, headers={"Location": f"https://example.invalid/{PRIVATE}"}),
         ElectricityAuthError, "重定向", {}),
        (lambda _: httpx.Response(401, text=PRIVATE), ElectricityAuthError, "HTTP 401", {}),
        (lambda _: httpx.Response(403, text=PRIVATE), ElectricityAuthError, "HTTP 403", {}),
        (lambda _: httpx.Response(304), ElectricityProtocolError, "HTTP 304", {}),
        (lambda _: httpx.Response(200, text=PRIVATE), ElectricityProtocolError, "非 JSON", {}),
        (lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=PRIVATE.encode()),
         ElectricityProtocolError, "无法解码", {}),
        (lambda _: httpx.Response(200, json={"returncode": "999", "returnmsg": PRIVATE}),
         ElectricityProtocolError, "查询成功状态", {}),
        (success(PRIVATE), ElectricityProtocolError, "无法解析", {}),
        (timeout, ElectricityNetworkError, "超时", {}),
        (disconnect, ElectricityNetworkError, "无法连接", {}),
    ),
)
def test_query_balance_returns_sanitized_errors(respond, error_type, expected, overrides):
    with pytest.raises(error_type) as exc_info:
        check(respond, **overrides)
    assert expected in str(exc_info.value)
    assert PRIVATE not in "".join(traceback.format_exception(exc_info.value))


ADDRESS_JSON = '{"campus":"金明","building":"东苑26栋","room":"638","meter":"lighting"}'


@pytest.mark.parametrize("stdin", [ADDRESS_JSON, "东苑26638照明", '"东苑２６６３８照明"'])
def test_cli_address_uses_official_lighting_id(monkeypatch, tmp_path, capsys, stdin):
    context = tmp_path / "context.json"
    context.write_text(json.dumps({
        "cookie": f"TEST={PRIVATE}",
        "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "directory": [{
            "campus": "金明校区", "building": "东苑26号公寓", "floor": "6层",
            "rooms": [{"id": "dir-aircon", "name": "638房间"}, {"id": "dir-lighting", "name": "26638房间"}],
        }],
    }), encoding="utf-8")
    context.chmod(0o600)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url).split("?", 1)[0] == ROOM_STATE
        assert dict(request.url.params) == {
            "roomverify": "dir-lighting", "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        }
        return success("67.36")(request)

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    assert main(["--context", str(context)]) == 0
    output = capsys.readouterr().out
    reading, = json.loads(output)["readings"]
    assert reading["meter"] == "lighting" and reading["quantity"] == "67.36"
    assert (reading["campus"], reading["building"], reading["room"]) == ("金明校区", "东苑26号公寓", "638")
    assert len(requests) == 1
    assert PRIVATE not in output and "dir-lighting" not in output


def test_cli_address_reports_missing_context(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(ADDRESS_JSON))
    assert main([]) == 1
    assert "--context" in capsys.readouterr().err


def test_cli_address_rejects_shared_context(monkeypatch, tmp_path, capsys):
    context = tmp_path / "shared.json"
    context.write_text(PRIVATE, encoding="utf-8")
    context.chmod(0o644)
    monkeypatch.setattr(sys, "stdin", io.StringIO(ADDRESS_JSON))
    assert main(["--context", str(context)]) == 1
    error = capsys.readouterr().err
    assert "独享" in error and PRIVATE not in error


@pytest.mark.parametrize(("text", "meters", "failures", "blocked"), [
    ("顺河15345", ("aircon", "lighting"), (), None),
    ("顺河15345空调和照明", ("aircon", "lighting"), (), None),
    ("顺河15345空调", ("aircon",), (), None),
    ("帮我查顺河15345的照明电量", ("lighting",), (), None),
    ("顺河15345", ("aircon", "lighting"), ("aircon",), None),
    ("顺河15345", ("aircon", "lighting"), ("lighting",), None),
    ("顺河15345", ("aircon", "lighting"), ("aircon", "lighting"), None),
    ("顺河15345", (), (), "未允许选房"),
    ("顺河15345", (), (), "重复电表项"),
])
def test_cli_shunhe_uses_live_directory(monkeypatch, tmp_path, capsys, text, meters, failures, blocked):
    context = tmp_path / "context.json"
    context.write_text(json.dumps({
        "cookie": f"TEST={PRIVATE}",
        "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
    }), encoding="utf-8")
    context.chmod(0o600)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    requests = []
    queried = []
    reading_by_meter = {"aircon": "42.18", "lighting": "12.35"}
    rows = {
        "1": ([{"id": "other-campus", "name": "金明校区"}, {"id": "area-from-server", "name": "明伦校区"}], {}),
        "2": ([{"id": "other-building", "name": "铁三16号公寓"}, {"id": "build-from-server", "name": "铁三15号公寓"}],
              {"areaid": "area-from-server"}),
        "3": ([{"id": "floor-from-server", "name": "3层"}],
              {"areaid": "area-from-server", "buildid": "build-from-server"}),
        "4": ([{"id": "aircon-from-server", "name": "345房间"}, {"id": "light-from-server", "name": "15345房间"}],
              {"areaid": "area-from-server", "buildid": "build-from-server", "levelid": "floor-from-server"}),
    }

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        params = dict(request.url.params)
        assert request.method == "GET"
        requests.append(url)
        if url == ROOM_ACCESS:
            assert params == {"schoolCode": "school"}
            return httpx.Response(200, json={"businessData": {"useFlag": "0" if blocked == "未允许选房" else "1"}})
        assert blocked != "未允许选房"
        if url == ROOM_CATALOG:
            if params["optype"] == "3" and params["buildid"] == "other-building":
                return httpx.Response(200, json={"code": "SUCCESS", "roomlist": []})
            items, parents = rows[params["optype"]]
            assert params == {
                "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
                "optype": params["optype"], "areaid": "0", "buildid": "0", "levelid": "0", "unitid": "0", **parents,
            }
            if params["optype"] == "4" and blocked == "重复电表项":
                items = [*items, {"id": "duplicate-from-server", "name": "15345房间"}]
            return httpx.Response(200, json={"code": "SUCCESS", "roomlist": items})
        assert url == ROOM_STATE and blocked is None
        meter = {"aircon-from-server": "aircon", "light-from-server": "lighting"}[params["roomverify"]]
        queried.append(meter)
        if meter in failures:
            if meter == "lighting":
                return success("-1")(request)
            raise httpx.ReadTimeout(PRIVATE, request=request)
        return success(reading_by_meter[meter])(request)

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    if blocked:
        assert main(["--context", str(context)]) == 1
        assert blocked in capsys.readouterr().err
        assert not queried
        return

    expected = {meter: reading_by_meter[meter] for meter in meters if meter not in failures}
    assert main(["--context", str(context)]) == (0 if expected else 1)
    output = capsys.readouterr().out
    result = json.loads(output)
    assert {row["meter"]: row["quantity"] for row in result["readings"]} == expected
    assert tuple(row["meter"] for row in result.get("errors", [])) == failures
    failure_text = {"aircon": "超时", "lighting": "未返回该表电量"}
    assert all(failure_text[row["meter"]] in row["error"] for row in result.get("errors", []))
    for row in result["readings"] + result.get("errors", []):
        assert (row["campus"], row["building"], row["floor"], row["room"]) == (
            "明伦校区", "铁三15号公寓", "3层", "345",
        )
    assert PRIVATE not in output and "-from-server" not in output
    assert tuple(queried) == meters
    assert requests == [ROOM_ACCESS, *([ROOM_CATALOG] * 5), *([ROOM_STATE] * len(meters))]


def test_cli_reports_expired_session_from_room_access(monkeypatch, tmp_path, capsys):
    context = tmp_path / "context.json"
    saved = {
        "cookie": f"TEST={PRIVATE}", "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
    }
    context.write_text(json.dumps(saved), encoding="utf-8")
    context.chmod(0o600)
    monkeypatch.setattr(sys, "stdin", io.StringIO("顺河15345"))

    def handle(request):
        assert str(request.url).split("?", 1)[0] == ROOM_ACCESS
        return httpx.Response(200, headers={"Set-Cookie": "TEST=anonymous; Path=/"}, json={
            "returncode": "-600", "returnmsg": "云缴费平台接口返回错误:idserial为空,", "businessData": None,
        })

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    assert main(["--context", str(context)]) == 1
    assert "过期" in capsys.readouterr().err
    assert json.loads(context.read_text(encoding="utf-8")) == saved


def test_query_saved_retries_after_refresh(monkeypatch, tmp_path):
    from campus_core.electricity_client import PAY_EXCHANGE, query_saved

    context = tmp_path / "context.json"
    saved = {
        "cookie": "SESSION=expired", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "session_id": "campus-session", "custom_id": "817",
        "directory": [{
            "campus": "金明校区", "building": "东苑26号公寓", "floor": "6层",
            "rooms": [{"id": "dir-aircon", "name": "638房间"}, {"id": "dir-lighting", "name": "26638房间"}],
        }],
    }
    context.write_text(json.dumps(saved), encoding="utf-8")
    context.chmod(0o600)
    seen = []

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        seen.append(url)
        if url == PAY_EXCHANGE and "token" in dict(request.url.params):
            return httpx.Response(302, headers={"Set-Cookie": "SESSION=fresh; Path=/",
                                                "Location": PAY_EXCHANGE + "?step=2"})
        if url == PAY_EXCHANGE:
            return httpx.Response(200, text="ok")
        if url == ROOM_ACCESS:
            if "SESSION=fresh" not in request.headers["Cookie"]:
                return httpx.Response(200, json={"returncode": "-600", "returnmsg": "idserial为空"})
            return httpx.Response(200, json={"businessData": {"useFlag": "1"}})
        assert url == ROOM_STATE
        if "SESSION=fresh" not in request.headers["Cookie"]:
            return httpx.Response(401)
        return success("7.5")(request)

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    outcomes = query_saved(context, {"campus": "金明", "building": "东苑26栋", "room": "638", "meter": "aircon"})
    assert [o["quantity"] for o in outcomes] == [Decimal("7.5")]
    stored = json.loads(context.read_text(encoding="utf-8"))
    assert "SESSION=fresh" in stored["cookie"] and stored["session_id"] == "campus-session"
    assert seen.count(PAY_EXCHANGE) == 2


def test_query_saved_without_session_id_raises(monkeypatch, tmp_path):
    from campus_core.electricity_client import query_saved

    context = tmp_path / "context.json"
    context.write_text(json.dumps({
        "cookie": "SESSION=expired", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "directory": [{"campus": "金明校区", "building": "东苑26号公寓", "floor": "6层",
                       "rooms": [{"id": "a", "name": "638房间"}]}],
    }), encoding="utf-8")
    context.chmod(0o600)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)), **kwargs))
    with pytest.raises(ElectricityAuthError):
        query_saved(context, {"campus": "金明", "building": "东苑26栋", "room": "638"})


def test_refresh_falls_back_to_token_login(monkeypatch, tmp_path):
    import campus_core.electricity_client as ec
    from campus_core.wmxy_auth import CampusSession

    context = tmp_path / "context.json"
    saved = {
        "cookie": "SESSION=stale; sid=x", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "session_id": "stale-session", "custom_id": "817", "login_token": "old-token",
        "user_id": "12345", "login_name": "138****", "device_id": "app-device",
    }
    context.write_text(json.dumps(saved), encoding="utf-8")
    context.chmod(0o600)

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        if url == ec.PAY_EXCHANGE and "token" in dict(request.url.params):
            if dict(request.url.params)["token"] != "fresh-session":
                return httpx.Response(200, text="出错啦")
            return httpx.Response(302, headers={"Set-Cookie": "SESSION=fresh; Path=/",
                                                "Location": ec.PAY_EXCHANGE + "?step=2"})
        if url == ec.PAY_EXCHANGE:
            return httpx.Response(200, text="ok")
        assert url == ec.ROOM_ACCESS
        return httpx.Response(200, json={"businessData": {"useFlag": "1"}})

    def fake_refresh(http, *, token, user_id, user_name, device=None):
        assert (token, user_id, user_name, device["deviceId"]) == ("old-token", "12345", "138****", "app-device")
        return CampusSession("0123456789abcdef01234567", "fresh-session"), {"token": "new-token", "customId": "817"}

    monkeypatch.setattr(ec, "login_and_refresh", fake_refresh)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    report = ec.refresh_saved(context)
    assert report["recognized"] is True and report["token_refreshed"] is True
    stored = json.loads(context.read_text(encoding="utf-8"))
    assert stored["login_token"] == "new-token" and stored["session_id"] == "fresh-session"
    assert "SESSION=fresh" in stored["cookie"]


@pytest.mark.parametrize("status", [302, 200])
def test_refresh_exchanges_session_id_for_paydo_cookie(monkeypatch, tmp_path, status):
    from campus_core.electricity_client import PAY_EXCHANGE, refresh_saved

    context = tmp_path / "context.json"
    saved = {
        "cookie": "SESSION=old; sid=encoded", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "session_id": "campus-session", "custom_id": "817",
    }
    context.write_text(json.dumps(saved), encoding="utf-8")
    context.chmod(0o600)
    seen = []

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        seen.append(url)
        if url == PAY_EXCHANGE and "token" in dict(request.url.params):
            params = dict(request.url.params)
            assert params["token"] in ("campus-session", "fresh-session")
            assert params["customerId"] == "817" and params["UAinfo"] == "wanxiao"
            assert request.headers["X-Requested-With"] == "com.newcapec.mobile.ncp"
            assert "SESSION=" not in request.headers["Cookie"]
            if status != 302:
                return httpx.Response(200, headers={"Set-Cookie": "SESSION=anonymous; Path=/"}, text="出错啦")
            return httpx.Response(302, headers={
                "Set-Cookie": "SESSION=fresh; Path=/", "Location": PAY_EXCHANGE + "?step=2"})
        if url == PAY_EXCHANGE:
            assert request.headers["Cookie"] == "sid=encoded; SESSION=fresh"
            return httpx.Response(200, text="ok")
        assert url == ROOM_ACCESS
        assert request.headers["Cookie"] == "sid=encoded; SESSION=fresh"
        return httpx.Response(200, json={"businessData": {"useFlag": "1"}})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    report = refresh_saved(context)
    assert report["recognized"] is (status == 302)
    stored = json.loads(context.read_text(encoding="utf-8"))
    assert stored == ({**saved, "cookie": "sid=encoded; SESSION=fresh"} if status == 302 else saved)
    assert seen == ([PAY_EXCHANGE, PAY_EXCHANGE, ROOM_ACCESS] if status == 302 else [PAY_EXCHANGE])


REFERER = ORIGIN + "/CloudPayment/bill/selectPayProject.do"
CURL = f"""curl '{ROOM_STATE}?businesstype=electricity&payProId=project&roomverify=dir-aircon&schoolcode=school' \\
  -H 'Accept: application/json' \\
  -b 'TEST={PRIVATE}; KEEP=1' \\
  -H 'Referer: {REFERER}?payProId=project' \\
  -H 'X-Requested-With: XMLHttpRequest' \\
  --compressed"""


def test_cli_import_curl_saves_private_context(monkeypatch, tmp_path, capsys):
    context = tmp_path / "private" / "context.json"
    monkeypatch.setattr(sys, "stdin", io.StringIO(CURL))
    assert main(["--context", str(context), "--import-curl"]) == 0
    assert context.stat().st_mode & 0o777 == 0o600
    data = json.loads(context.read_text(encoding="utf-8"))
    assert data == {
        "cookie": f"TEST={PRIVATE}; KEEP=1", "referer": REFERER + "?payProId=project",
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
    }
    assert PRIVATE not in "".join(capsys.readouterr())

    # 重新导入只换会话，保留已有目录快照。
    context.write_text(json.dumps({**data, "cookie": "OLD=1", "directory": []}), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(CURL))
    assert main(["--context", str(context), "--import-curl"]) == 0
    assert json.loads(context.read_text(encoding="utf-8")) == {**data, "directory": []}

    monkeypatch.setattr(sys, "stdin", io.StringIO(CURL.replace("getRoomState", "getRoom")))
    assert main(["--context", str(context), "--import-curl"]) == 1
    assert "getRoomState.do" in capsys.readouterr().err


@pytest.mark.parametrize("status", [200, 403])
def test_cli_saves_rotated_cookie_after_success(monkeypatch, tmp_path, capsys, status):
    context = tmp_path / "context.json"
    saved = {
        "cookie": f"TEST={PRIVATE}; KEEP=1", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "directory": [{
            "campus": "金明校区", "building": "东苑26号公寓", "floor": "6层",
            "rooms": [{"id": "dir-aircon", "name": "638房间"}, {"id": "dir-lighting", "name": "26638房间"}],
        }],
    }
    context.write_text(json.dumps(saved), encoding="utf-8")
    context.chmod(0o600)
    monkeypatch.setattr(sys, "stdin", io.StringIO("东苑26638照明"))

    def handle(request):
        assert request.headers["Cookie"] == saved["cookie"]
        headers = [("Set-Cookie", "TEST=rotated; Path=/"), ("Set-Cookie", "NEW=2; Path=/")]
        if status != 200:
            return httpx.Response(status, headers=headers)
        return httpx.Response(200, headers=headers, json={"returncode": "100", "quantity": "1", "quantityunit": "度"})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    assert main(["--context", str(context)]) == (0 if status == 200 else 1)
    stored = json.loads(context.read_text(encoding="utf-8"))
    assert stored == ({**saved, "cookie": "TEST=rotated; KEEP=1; NEW=2"} if status == 200 else saved)
    assert context.stat().st_mode & 0o777 == 0o600
    assert "rotated" not in "".join(capsys.readouterr())


@pytest.mark.parametrize("status", [200, 403])
def test_bot_command_uses_operator_context(monkeypatch, tmp_path, caplog, status):
    from henu_plugin.service import HenuPluginService

    context = tmp_path / "operator.json"
    context.write_text(json.dumps({
        "cookie": f"TEST={PRIVATE}", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
        "directory": [{
            "campus": "金明校区", "building": "东苑26号公寓", "floor": "6层",
            "rooms": [{"id": "dir-aircon", "name": "638房间"}, {"id": "dir-lighting", "name": "26638房间"}],
        }],
    }), encoding="utf-8")
    context.chmod(0o600)
    monkeypatch.setenv("HENU_ELECTRICITY_CONTEXT", str(context))

    def handle(request):
        if status != 200:
            return httpx.Response(status)
        quantity = {"dir-aircon": "42.18", "dir-lighting": "12.35"}[request.url.params["roomverify"]]
        return success(quantity)(request)

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    caplog.set_level("INFO", logger="httpx")
    result = HenuPluginService(tmp_path)._henu_cli({"command": "electricity 东苑26 638"})
    if status == 200:
        assert result["success"] is True
        assert result["reply_text"] == "金明校区 东苑26号公寓 638\n空调：剩余 42.18 度\n照明：剩余 12.35 度"
    else:
        assert result["success"] is False and "重新导入" in result["msg"]
    assert "dir-" not in json.dumps(result, ensure_ascii=False) and PRIVATE not in json.dumps(result)
    assert not [record for record in caplog.records if "dir-" in record.getMessage()]

    monkeypatch.delenv("HENU_ELECTRICITY_CONTEXT")
    monkeypatch.setattr("henu_plugin.service.ENV_FILE", tmp_path / "missing.env")
    assert "未配置" in HenuPluginService(tmp_path)._henu_cli({"command": "electricity 东苑26 638"})["msg"]
