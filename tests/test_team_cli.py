"""Happy-path tests for `reflect team` subcommands (init/clone/sync).

Git network operations are avoided — we validate the local scaffold and config
flow. clone/sync behavior that wraps `git clone`/`git pull` is covered by a
loopback test against a locally-initialized bare repo to keep the suite
hermetic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from reflect_kb.cli import team as team_mod
from reflect_kb.cli.team import team


def _redirect_config(tmp_path: Path, monkeypatch) -> Path:
    cfg_path = tmp_path / "team-config.yaml"
    monkeypatch.setattr(team_mod, "TEAM_CONFIG_PATH", cfg_path)
    return cfg_path


def test_team_init_scaffolds_expected_files(tmp_path, monkeypatch):
    cfg_path = _redirect_config(tmp_path, monkeypatch)
    target = tmp_path / "my-team-kb"

    result = CliRunner().invoke(team, ["init", str(target), "--name", "my-team"])
    assert result.exit_code == 0, result.output

    for rel in [
        "documents",
        "README.md",
        "CODEOWNERS",
        ".pre-commit-config.yaml",
        ".github/workflows/validate-frontmatter.yml",
        ".gitignore",
    ]:
        assert (target / rel).exists(), f"expected {rel} in scaffold"

    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["name"] == "my-team"
    assert Path(cfg["path"]) == target


def test_team_init_is_idempotent(tmp_path, monkeypatch):
    _redirect_config(tmp_path, monkeypatch)
    target = tmp_path / "kb"
    runner = CliRunner()

    first = runner.invoke(team, ["init", str(target)])
    assert first.exit_code == 0

    # Hand-edit a scaffolded file and re-run init. The second run must not
    # overwrite user content.
    readme = target / "README.md"
    readme.write_text("custom content", encoding="utf-8")

    second = runner.invoke(team, ["init", str(target)])
    assert second.exit_code == 0
    assert readme.read_text() == "custom content"


def test_team_clone_uses_loopback_repo(tmp_path, monkeypatch):
    cfg_path = _redirect_config(tmp_path, monkeypatch)
    # Skip the reindex stage — the ``reflect`` binary may not be on PATH in
    # CI, and reindex correctness is covered by its own tests.
    monkeypatch.setattr(team_mod, "_reindex", lambda root: None)

    # Build a local bare repo to clone from, so the test never hits the network.
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
    )
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"], cwd=source, check=True
    )
    subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "t"], check=True)
    (source / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "push", "-q", str(origin), "HEAD:main"],
        check=True,
        capture_output=True,
    )

    dest = tmp_path / "team-clone"
    result = CliRunner().invoke(
        team, ["clone", str(origin), "--path", str(dest)]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "README.md").exists()

    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["url"] == str(origin)
    assert Path(cfg["path"]) == dest


def test_team_sync_errors_when_unconfigured(tmp_path, monkeypatch):
    _redirect_config(tmp_path, monkeypatch)
    result = CliRunner().invoke(team, ["sync"])
    assert result.exit_code != 0
    assert "no configured team" in result.output.lower()
