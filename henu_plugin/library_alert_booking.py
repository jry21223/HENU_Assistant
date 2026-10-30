"""Single-attempt reservation using the alert's ownership-proven saved session."""

from henu_plugin.library_alert_query import AlertQueryError


def require_owned_session(snapshot):
    if (
        not snapshot.subject
        or snapshot.cookies.get("_subject_student_id") != snapshot.subject
    ):
        raise AlertQueryError(
            "session_changed",
            "当前登录态尚未核验账号归属，请先手动查询图书馆座位，再重新设置。",
        )


def reserve_snapshot(snapshot, plan, seat, deadline, expired):
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

    class BookingSession(requests.Session):
        def request(self, method, url, **kwargs):
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
            kwargs.update(allow_redirects=False, timeout=25)
            return super().request(method, url, **kwargs)

    headers = dict(bot.session.headers)
    bot.session.close()
    bot.session = BookingSession()
    bot.session.headers.update(headers)
    bot.session.cookies.update(
        {k: v for k, v in cookies.items() if not k.startswith("_")}
    )
    bot._set_auth_header()
    try:
        return bot.reserve(
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
    finally:
        bot.session.close()
