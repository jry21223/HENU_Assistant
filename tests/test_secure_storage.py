from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from henu_mcp.core import secure_storage


_WINDOWS_LIKE_CHILD = r"""
import os
from pathlib import Path
import sys

from henu_mcp.core import secure_storage


class WindowsLikeOS:
    environ = os.environ

    @staticmethod
    def getpid():
        return 10101 if sys.argv[1] == "write" else 20202


secure_storage.os = WindowsLikeOS()
secure_storage.platform.node = lambda: "stable-windows-host"
secure_storage.platform.system = lambda: "Windows"
secure_storage.platform.machine = lambda: "AMD64"

profile_path = Path(sys.argv[2])
if sys.argv[1] == "write":
    secure_storage.save_encrypted_profile(
        profile_path,
        {"student_id": "1", "password": "cross-process-secret"},
    )
else:
    print(secure_storage.load_encrypted_profile(profile_path)["password"])
"""

_LEGACY_TOKEN = (
    "enc:gAAAAABqeIqmOh7DuByoff9lHPfMkJelASR1qc7A1F_Dk2IKI_"
    "cpymZTuj_C8flhN6sdHBCbuTbxpEXW3m49FAfyeyTZlQrmGw=="
)


def _run_windows_like_child(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("HENU_MASTER_KEY", None)
    environment["HOME"] = str(Path(arguments[-1]).parent / "home")
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo_root), environment.get("PYTHONPATH", ""))
        if part
    )
    return subprocess.run(
        [sys.executable, "-c", _WINDOWS_LIKE_CHILD, *arguments],
        cwd=repo_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_windows_like_processes_share_stable_fallback_key(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    profile_path = tmp_path / "profile.json"

    _run_windows_like_child(repo_root, "write", str(profile_path))
    on_disk = json.loads(profile_path.read_text(encoding="utf-8"))
    reader = _run_windows_like_child(repo_root, "read", str(profile_path))

    assert on_disk["password"].startswith("enc:v2:")
    assert reader.stdout.strip() == "cross-process-secret"


def test_wrong_master_key_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HENU_MASTER_KEY", "key-a")
    token = secure_storage.encrypt_value("secret")

    monkeypatch.setenv("HENU_MASTER_KEY", "key-b")

    with pytest.raises(
        secure_storage.CredentialDecryptionError,
        match="HENU_MASTER_KEY",
    ):
        secure_storage.decrypt_value(token)


def test_legacy_enc_value_remains_decryptable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secure_storage.platform, "node", lambda: "legacy-host")
    monkeypatch.setattr(secure_storage.platform, "system", lambda: "Linux")
    monkeypatch.setattr(secure_storage.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(secure_storage.os, "getuid", lambda: 1000)
    monkeypatch.setattr(
        secure_storage.Path,
        "home",
        classmethod(lambda cls: cls("/home/legacy")),
    )

    assert secure_storage.decrypt_value(_LEGACY_TOKEN) == "legacy-secret"


def test_corrupt_legacy_enc_value_fails_explicitly() -> None:
    with pytest.raises(
        secure_storage.CredentialDecryptionError,
        match="旧版账号密码无法解密",
    ):
        secure_storage.decrypt_value("enc:not-a-fernet-token")


def test_saved_profile_records_the_v2_key_version(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"

    secure_storage.save_encrypted_profile(
        profile_path,
        {"student_id": "1", "password": "secret"},
    )

    on_disk = json.loads(profile_path.read_text(encoding="utf-8"))
    assert on_disk["credential_key_version"] == "v2"


def test_existing_malformed_profile_fails_closed(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="账号配置 JSON 损坏"):
        secure_storage.load_encrypted_profile(profile_path)
    assert profile_path.read_text(encoding="utf-8") == "{broken"
