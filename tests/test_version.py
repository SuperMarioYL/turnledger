"""v0.2.0 tests — version lockstep across every version surface.

Asserts VERSION file, pyproject.toml [project].version, package
``__version__``, CLI ``--version`` / ``info`` output, and web/site.json
``meta.implementation_version`` all report the same version. pyproject is
parsed with a regex (not tomllib) so the test also runs on Python 3.10.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

import turnledger
from turnledger.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.3.0"

runner = CliRunner()


def test_version_lockstep() -> None:
    version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version_file == EXPECTED

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None, "pyproject.toml has no [project] version"
    assert match.group(1) == EXPECTED

    assert turnledger.__version__ == EXPECTED

    flagged = runner.invoke(app, ["--version"])
    assert flagged.exit_code == 0, flagged.output
    assert EXPECTED in flagged.output

    info = runner.invoke(app, ["info"])
    assert info.exit_code == 0, info.output
    assert EXPECTED in info.output


def test_site_json_implementation_version_matches() -> None:
    site = json.loads((REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8"))
    # Key already exists in the manifest — assert lockstep, add no new keys.
    assert site["meta"]["implementation_version"] == EXPECTED
