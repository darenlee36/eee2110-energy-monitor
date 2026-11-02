"""Launcher repair: no inference or real scheduled-task mutations in tests."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_reporting import seeded_cycles


def test_no_ai_pass_generates_cycles_without_quota_or_provider(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path, 2)

    def forbidden(*_):
        pytest.fail("No-AI verification invoked Codex/quota/provider")

    result = run_reporting_pass(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        state_dir=tmp_path / "state",
        usage_reader=forbidden,
        runner=forbidden,
        allow_ai=False,
    )
    assert result["status"] == "no_ai" and result["ai_attempts"] == 0
    assert result["templates_created"] == 2
    assert not (tmp_path / "state/attempts").exists()
    again = run_reporting_pass(
        store,
        profile_id="tefal-kettle",
        device_id="monitor-001",
        state_dir=tmp_path / "state",
        usage_reader=forbidden,
        runner=forbidden,
        allow_ai=False,
    )
    assert again["templates_created"] == 0 and len(store.fetch_reports()) == 2


def test_cli_no_ai_is_explicit_and_cannot_be_combined_with_ai_permission(
    tmp_path, monkeypatch, capsys
):
    import energy_monitor.report_worker as module
    from energy_monitor.storage import SQLiteTelemetryStore

    store, _ = seeded_cycles(tmp_path)

    def forbidden(*_):
        pytest.fail("No-AI mode invoked inference or quota")

    monkeypatch.setattr(module, "read_codex_usage", forbidden)
    monkeypatch.setattr("energy_monitor.report_narrative.run_codex", forbidden)
    args = [
        "--profile-id",
        "tefal-kettle",
        "--device-id",
        "monitor-001",
        "--source",
        "local",
        "--database",
        str(store.database_path),
        "--state-dir",
        str(tmp_path / "state"),
        "--no-ai",
    ]
    assert module.main(args) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "no_ai" and status["ai_attempts"] == 0
    assert len(SQLiteTelemetryStore(store.database_path).fetch_reports()) == 1
    with pytest.raises(SystemExit):
        module.main([*args, "--allow-one-codex-call"])


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher discovery")
def test_discovery_handles_removed_version_without_interactive_path(tmp_path):
    root = tmp_path / "app-bin"
    root.mkdir()
    for name in ("old-removed", "current"):
        (root / name).mkdir()
    current = root / "current/codex.exe"
    current.write_bytes(b"fixture: never execute")
    script = Path(__file__).parents[1] / "scripts/reporting-schedule.ps1"
    code = (
        f". '{script}' -Action Plan -NoAI | Out-Null\n"
        + r"""
    function Get-Command { param($Name) return $null }
    Resolve-ReportingCodex -BinRoot $args[0]
    """
    )
    code = code.replace("$args[0]", "'" + str(root).replace("'", "''") + "'")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == current


@pytest.mark.skipif(os.name != "nt", reason="Native launcher entry point")
def test_powershell_worker_loads_this_checkout_and_is_numerical_only(tmp_path):
    store, _ = seeded_cycles(tmp_path)
    script = Path(__file__).parents[1] / "scripts/reporting-schedule.ps1"
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Action",
            "Worker",
            "-NoAI",
            "-Source",
            "local",
            "-Database",
            str(store.database_path),
            "-StateDirectory",
            str(tmp_path / "state"),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads((tmp_path / "state/status.json").read_text())
    assert status["status"] == "no_ai" and status["ai_attempts"] == 0
    assert (
        Path(status["worker_source"]).resolve()
        == (Path(__file__).parents[1] / "src/energy_monitor/report_worker.py").resolve()
    )
