from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_environment_setup_revalidates_an_existing_venv() -> None:
    source = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "if [ ! -x .venv/bin/python ]; then" not in source
    assert "python3 -m venv --clear .venv" in source
    assert 'LOCK_FILE="$(.venv/bin/python scripts/select_lockfile.py --check)"' in source
    assert ".venv/bin/python -m pip install --require-hashes" in source
    assert 'version("mcp") == "2.0.0"' in source


def test_skill_documents_the_posix_only_release_scope() -> None:
    documents = [
        (ROOT / "SKILL.md").read_text(encoding="utf-8"),
        (ROOT / "README.md").read_text(encoding="utf-8"),
    ]

    for source in documents:
        assert "Windows 不在 2.1.0" in source
        assert "--platform windows" not in source
