"""Regression checks for PlaylistAI release metadata and build toolchains."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_matches_application_and_changelog():
    version = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)

    app_source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', app_source, re.M)
    assert match is not None
    assert match.group(1) == version

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_version = re.search(r"^##\s+(\d+\.\d+\.\d+)\s*$", changelog, re.M)
    assert first_version is not None
    assert first_version.group(1) == version


def test_mac_and_windows_build_use_same_python_patch():
    versions = []
    for relative_path in ("scripts/build_macos.sh", "scripts/build_windows.ps1"):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        match = re.search(r"uv venv --python (\d+\.\d+\.\d+)", source)
        assert match is not None, relative_path
        versions.append(match.group(1))

    assert versions[0] == versions[1]
