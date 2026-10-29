"""Выпуск: одна версия везде, раздел CHANGELOG для неё, скрипт установки агента."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from expsched.agent.core import VERSION

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install-agent.sh"
BASH = shutil.which("bash")  # полный путь: на Windows голое «bash» — это WSL из системного каталога


def test_one_version_everywhere():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    web = json.loads((ROOT / "ui" / "web" / "package.json").read_text(encoding="utf-8"))["version"]
    script = re.search(r'^VERSION="([^"]+)"', SCRIPT.read_text(encoding="utf-8"), re.M)
    newest = re.search(r"^## \[([^\]]+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert script and newest
    assert project == VERSION == web == script[1] == newest[1]


def test_release_workflow_runs_on_version_tags_and_publishes_images():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in workflow and "packages: write" in workflow
    assert "containers/workload/Containerfile" in workflow and "scripts/install-agent.sh" in workflow


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_install_script_dry_run_shows_what_it_would_do(tmp_path):
    args = [
        "--repo",
        "Someone/expsched",
        "--id",
        "gpu-box",
        "--scheduler",
        "http://10.0.0.5:8000",
        "--dry-run",
    ]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": "",
        "XDG_CONFIG_HOME": "",
        "EXPSCHED_AGENT_TOKEN": "t",
    }
    out = subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, check=True
    ).stdout
    assert f"releases/download/v{VERSION}/expsched-{VERSION}-py3-none-any.whl" in out
    assert f"docker pull ghcr.io/someone/expsched-workload:{VERSION}" in out
    assert "docker tag" in out and "expsched-workload:local" in out
    assert (
        'id = "gpu-box"' in out
        and 'scheduler = "http://10.0.0.5:8000"' in out
        and "token" not in out.split("[agent]")[1].split("token_file")[0]
    )
    refused = subprocess.run([BASH, str(SCRIPT), "--id", "x"], capture_output=True, text=True, env=env)
    assert refused.returncode == 1 and "--repo" in refused.stderr
