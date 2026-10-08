from __future__ import annotations

from campus_core.cas_page import classify_cas_login_page, extract_cas_login_fields


def test_cas_fields_are_parsed_independent_of_attribute_order_and_quotes() -> None:
    html = """
    <html><head><title>统一身份认证</title></head><body>
      <input value='e1s1' type='hidden' name='execution'>
      <input value="salt-value" name="pwdEncryptSalt" id="pwdEncryptSalt">
    </body></html>
    """

    assert extract_cas_login_fields(html) == {
        "execution": "e1s1",
        "pwd_encrypt_salt": "salt-value",
    }


def test_cas_missing_fields_are_classified_without_claiming_school_redesign() -> None:
    code, message = classify_cas_login_page(
        "<html><title>安全验证</title><body>请完成验证码 challenge</body></html>",
        status_code=200,
        final_url="https://ids.henu.edu.cn/authserver/login",
    )

    assert code == "cas_challenge_required"
    assert "额外验证" in message
    assert "学校页面已改版" not in message


def test_cas_http_rejection_is_distinct_from_schema_incompatibility() -> None:
    blocked = classify_cas_login_page(
        "forbidden",
        status_code=403,
        final_url="https://ids.henu.edu.cn/authserver/login",
    )
    schema = classify_cas_login_page(
        "<html><title>统一认证</title><body>unknown form</body></html>",
        status_code=200,
        final_url="https://ids.henu.edu.cn/authserver/login",
    )

    assert blocked[0] == "cas_blocked"
    assert schema[0] == "cas_schema_incompatible"
    assert blocked[1] != schema[1]
