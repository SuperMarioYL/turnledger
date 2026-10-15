"""Version-consistency tests — every version surface must report the same release.

Extends the v0.2.0 lockstep (tests/test_version.py) to ALL version-carrying
surfaces: VERSION file, pyproject.toml [project].version, package
``__version__``, CLI ``--version``, both README hero lines, web/site.json
``meta.implementation_version``, and the CHANGELOG release sections. The
installed-binary check shells out to ``turnledger`` and is skipped when the
binary is not on PATH (GH runners without an install stay green).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import turnledger
from turnledger.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"

runner = CliRunner()


def test_version_file() -> None:
    assert (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip() == VERSION


def test_pyproject_version() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None, "pyproject.toml has no [project] version"
    assert match.group(1) == VERSION


def test_package_dunder_version() -> None:
    assert turnledger.__version__ == VERSION


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"turnledger {VERSION}"


@pytest.mark.parametrize("readme", ["README.md", "README.en.md"])
def test_readme_hero_line_carries_version(readme: str) -> None:
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    assert f"`v{VERSION}`" in text, f"{readme} hero line missing v{VERSION}"


def test_site_json_version() -> None:
    site = json.loads((REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8"))
    assert site["meta"]["implementation_version"] == VERSION


def test_changelog_sections_and_release_links() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{VERSION}]" in text
    assert "## [0.2.0]" in text  # historical sections are kept
    assert f"[{VERSION}]: https://github.com/SuperMarioYL/turnledger/releases/tag/v{VERSION}" in text


@pytest.mark.skipif(
    shutil.which("turnledger") is None,
    reason="turnledger binary not on PATH",
)
def test_installed_cli_version() -> None:
    out = subprocess.run(
        ["turnledger", "--version"], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"turnledger {VERSION}"
