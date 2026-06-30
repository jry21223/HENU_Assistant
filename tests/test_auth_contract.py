from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = ROOT / "henu_mcp" / "tools" / "server_impl.py"


def _function_source(name: str) -> str:
    source = MCP_SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function not found: {name}")


def test_account_password_paths_use_decrypted_profile() -> None:
    functions = [
        "_process_seminar_signin_tasks",
        "_library_locations_impl",
        "_library_seats_impl",
        "_library_reserve_impl",
        "_library_records_impl",
        "_library_current_impl",
        "_library_auto_signin_impl",
        "_seminar_filters_impl",
        "_seminar_records_impl",
        "_seminar_rooms_impl",
        "_seminar_room_detail_impl",
        "_seminar_reserve_impl",
        "_seminar_signin_impl",
        "_seminar_cancel_impl",
        "_library_cancel_impl",
        "yunfz_leave_query",
        "yunfz_signin_query",
        "yunfz_checksleep_query",
        "yunfz_activity_query",
        "yunfz_collection_query",
    ]

    offenders = [
        name
        for name in functions
        if "load_json(PROFILE_FILE)" in _function_source(name)
    ]

    assert offenders == []


def test_ids_cas_cookie_reuse_contract_is_present() -> None:
    library_source = _function_source("_build_library_bot")
    hebao_source = _function_source("_build_hebao_bot")

    assert "_load_cas_cookies()" in library_source
    assert "_load_cas_cookies()" in hebao_source
    assert 'cas_cookies.get("CASTGC"' in library_source
    assert "_save_cas_cookies(bot.get_cas_cookies())" in library_source
    assert "_save_cas_cookies(bot.get_hebao_cas_cookies())" in hebao_source
