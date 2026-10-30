"""Single-attempt reservation using the alert's ownership-proven saved session."""

from henu_plugin.library_alert_query import AlertQueryError, seat_identity
from henu_plugin.seat_map_image import seat_state


def require_owned_session(snapshot):
    if (
        not snapshot.subject
        or snapshot.cookies.get("_subject_student_id") != snapshot.subject
    ):
        raise AlertQueryError(
            "session_changed",
            "当前登录态尚未核验账号归属，请先手动查询图书馆座位，再重新设置。",
        )


def reserve_snapshot(snapshot, plan, seat, deadline, expired, before_submit):
    """Caller owns the user lock and has durably claimed this single attempt."""
    from campus_core.bot import HenuCampusBot
    import requests
    from urllib.parse import urlsplit

    require_owned_session(snapshot)
    cookies = {
        key: value
        for key, value in snapshot.cookies.items()
        if key == "_v4_token" or not key.startswith("_")
    }
    bot = HenuCampusBot(snapshot.subject, "", cookies)
    bot._saved_session_only = True
    submitted = False
    checked_seat = False

    class BookingSession(requests.Session):
        def request(self, method, url, **kwargs):
            nonlocal submitted, checked_seat
            parsed = urlsplit(url)
            if (
                method.upper() != "POST"
                or parsed.scheme != "https"
                or parsed.netloc != "zwyy.henu.edu.cn"
                or parsed.path
                not in {
                    "/v4/space/pick",
                    "/v4/Space/map",
                    "/v4/Space/seat",
                    "/v4/space/confirm",
                    "/v4/index/subscribe",
                }
            ):
                raise ValueError("Unexpected alert reservation endpoint")
            if expired():
                raise TimeoutError("Alert reservation deadline exceeded")
            if parsed.path == "/v4/space/confirm":
                if not checked_seat or not before_submit() or expired():
                    raise ValueError("Reservation authorization was revoked or expired")
                submitted = True
            kwargs.update(allow_redirects=False, timeout=25)
            response = super().request(method, url, **kwargs)
            if parsed.path in {"/v4/space/pick", "/v4/Space/map", "/v4/Space/seat"}:
                payload = response.json()
                if (
                    not isinstance(payload, dict)
                    or type(payload.get("code")) is not int
                    or payload["code"] != 0
                ):
                    raise ValueError("School preflight response is unknown")
                if parsed.path == "/v4/Space/seat":
                    rows = payload["data"]["list"]
                    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
                        raise ValueError("School seat response is incomplete")
                    matches = [
                        row
                        for row in rows
                        if isinstance(row, dict)
                        and seat_identity(row.get("no")) == seat_identity(seat)
                    ]
                    if (
                        len(matches) != 1
                        or seat_state(matches[0]) != "free"
                        or type(matches[0].get("id")) not in (str, int)
                        or not str(matches[0]["id"]).strip()
                    ):
                        raise ValueError("Selected seat is not reliably available")
                    checked_seat = True
            return response

    headers = dict(bot.session.headers)
    bot.session.close()
    bot.session = BookingSession()
    bot.session.headers.update(headers)
    bot.session.cookies.update(
        {k: v for k, v in cookies.items() if not k.startswith("_")}
    )
    bot._set_auth_header()
    try:
        result = bot.reserve(
            plan["area"]["area_id"],
            seat,
            plan["target_date"],
            preferred_time=plan["start"],
            preferred_end_time=plan["end"],
            strict_window=True,
            max_attempts=1,
            retry_until="",
            submit_before=deadline.isoformat(),
        )
        return {**result, "not_submitted": not submitted}
    finally:
        bot.session.close()
