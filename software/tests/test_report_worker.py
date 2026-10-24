import json
import os
import subprocess
import sys

import pytest
from test_reporting import seeded_cycles


def quota(primary=10, secondary=20):
    return {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": primary, "windowDurationMins": 300,
                    "resetsAt": 9999999999},
        "secondary": {"usedPercent": secondary, "windowDurationMins": 10080,
                      "resetsAt": 9999999999},
    }}}


@pytest.mark.parametrize("first,second", [(95, 0), (0, 95), (100, 100), (float('nan'), 0)])
def test_either_usage_window_blocks_at_five_percent(first, second):
    from energy_monitor.report_worker import UsageGuardError, quota_remaining

    with pytest.raises(UsageGuardError):
        quota_remaining(quota(first, second))


@pytest.mark.parametrize("payload", [{}, {"rateLimitsByLimitId": {}},
    {"rateLimits": {"primary": None, "secondary": None}},
    {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": False},
                                       "secondary": {"usedPercent": 0}}}},
])
def test_unknown_usage_never_means_zero_usage(payload):
    from energy_monitor.report_worker import UsageGuardError, quota_remaining

    with pytest.raises(UsageGuardError):
        quota_remaining(payload)


def test_usage_guard_accepts_both_verified_windows_and_rejects_stale_reset():
    from energy_monitor.report_worker import UsageGuardError, quota_remaining

    assert quota_remaining(quota(10, 20)) == (90, 80)
    stale = quota()
    stale["rateLimitsByLimitId"]["codex"]["primary"]["resetsAt"] = 1
    with pytest.raises(UsageGuardError):
        quota_remaining(stale)


def test_worker_catches_up_numerical_reports_but_caps_ai_to_one_per_run(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path, 3)
    calls = []

    def choose(catalog):
        calls.append(catalog)
        return {"observations": ["data_origin"], "actions": ["compare_conditions"]}

    options = {"profile_id": "tefal-kettle", "device_id": "monitor-001",
               "state_dir": tmp_path / "worker", "usage_reader": quota, "runner": choose}
    result = run_reporting_pass(store, **options)
    assert result["templates_created"] == 3
    assert result["ai_attempts"] == 1
    assert len(calls) == 1
    run_reporting_pass(store, **options)
    run_reporting_pass(store, **options)
    final = run_reporting_pass(store, **options)
    assert len(calls) == 3
    assert final["status"] == "idle"
    assert len(store.fetch_reports()) == 6


def test_low_usage_keeps_numerical_reports_without_an_ai_completion_marker(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path, 2)
    result = run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        usage_reader=lambda: quota(95, 0), runner=lambda _: pytest.fail("AI ran at cutoff"),
    )
    assert result["status"] == "usage_limit"
    assert result["templates_created"] == 2
    assert len(store.fetch_reports()) == 2
    assert not list((tmp_path / "worker" / "attempts").glob("*.json"))


def test_crash_reservation_prevents_repeat_inference_after_restart(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path)

    def interrupted(_):
        raise RuntimeError("Interrupted after an invocation may have started")

    options = {"profile_id": "tefal-kettle", "device_id": "monitor-001",
               "state_dir": tmp_path / "worker", "usage_reader": quota}
    with pytest.raises(RuntimeError):
        run_reporting_pass(store, **options, runner=interrupted)
    result = run_reporting_pass(store, **options,
                                runner=lambda _: pytest.fail("Interrupted report was retried"))
    assert result["status"] == "needs_review"
    assert result["held_attempts"] == 1
    assert len(store.fetch_reports()) == 1


def test_usage_falling_before_actual_call_does_not_leave_a_false_reservation(tmp_path):
    from energy_monitor.report_worker import UsageGuardError, run_reporting_pass

    store, _ = seeded_cycles(tmp_path)

    def paused(_):
        raise UsageGuardError("usage_limit")

    result = run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        usage_reader=quota, runner=paused,
    )
    assert result["status"] == "usage_limit"
    assert not list((tmp_path / "worker" / "attempts").glob("*.json"))


def test_worker_lock_blocks_another_process_and_releases_on_exit(tmp_path):
    from energy_monitor.report_worker import worker_lock

    lock = tmp_path / "worker.lock"
    code = (
        "from pathlib import Path; from energy_monitor.report_worker import worker_lock; "
        f"\nwith worker_lock(Path({str(lock)!r})) as acquired: print(acquired)"
    )
    with worker_lock(lock) as acquired:
        assert acquired
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "False"
    with worker_lock(lock) as acquired:
        assert acquired


def test_expired_worker_never_syncs_or_calls_ai(tmp_path):
    from datetime import UTC, datetime

    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path)
    result = run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        usage_reader=lambda: pytest.fail("Expired worker queried quota"),
        runner=lambda _: pytest.fail("Expired worker called AI"),
        prepare=lambda: pytest.fail("Expired worker synced"),
        now=datetime(2026, 12, 7, tzinfo=UTC),
    )
    assert result["status"] == "expired"
    assert not store.fetch_reports()


def test_status_file_contains_only_result_metadata(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path)
    result = run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        usage_reader=quota,
        runner=lambda _: {"observations": ["data_origin"], "actions": ["avoid_repeats"]},
    )
    status = json.loads((tmp_path / "worker" / "status.json").read_text())
    assert status["ai_attempts"] == result["ai_attempts"] == 1
    assert "evidence_snapshot" not in status
    assert "report_text" not in status


def test_cli_requires_explicit_ai_permission_and_valid_scope(tmp_path):
    from energy_monitor.report_worker import main

    common = ["--profile-id", "tefal-kettle", "--device-id", "monitor-001",
              "--database", str(tmp_path / "empty.db"), "--source", "local"]
    with pytest.raises(SystemExit):
        main(common)
    with pytest.raises(SystemExit):
        main([*common, "--allow-one-codex-call", "--device-id", "bad/device"])
    assert not (tmp_path / "empty.db").exists()


def test_cli_local_pass_uses_shared_guard_and_is_duplicate_safe(tmp_path, monkeypatch, capsys):
    import energy_monitor.report_worker as module

    store, _ = seeded_cycles(tmp_path, 2)
    monkeypatch.setattr(module, "read_codex_usage", quota)
    import energy_monitor.report_narrative as narrative

    calls = []
    def choose(_):
        calls.append(1)
        return {"observations": ["data_origin"], "actions": ["avoid_repeats"]}
    monkeypatch.setattr(narrative, "run_codex", choose)
    arguments = ["--profile-id", "tefal-kettle", "--device-id", "monitor-001",
                 "--database", str(store.database_path), "--source", "local",
                 "--state-dir", str(tmp_path / "worker"), "--allow-one-codex-call"]
    assert module.main(arguments) == 0
    assert len(calls) == 1
    assert module.main(arguments) == 0
    assert module.main(arguments) == 0
    assert len(calls) == 2
    assert len(store.fetch_reports()) == 4
    assert "report_text" not in capsys.readouterr().out


def test_explicit_codex_path_works_without_interactive_shell_path(monkeypatch, tmp_path):
    from pathlib import Path

    import energy_monitor.report_worker as module

    executable = tmp_path / "codex.exe"
    executable.touch()
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    monkeypatch.delenv("ENERGY_MONITOR_CODEX_EXECUTABLE", raising=False)
    assert module.codex_executable() is None
    monkeypatch.setenv("ENERGY_MONITOR_CODEX_EXECUTABLE", str(executable))
    assert Path(module.codex_executable()) == executable


def test_cloud_sync_backlog_is_bounded_before_any_ai_call(tmp_path, monkeypatch):
    import energy_monitor.report_worker as module
    import energy_monitor.supabase_sync as sync

    store, _ = seeded_cycles(tmp_path)
    calls = []
    def page(cache, config):
        calls.append(1)
        cache.save_cloud_sync_cursor(config.url, "2026-10-01T00:00:00+00:00",
                                     f"00000000-0000-0000-0000-{len(calls):012d}")
    monkeypatch.setattr(sync, "sync_supabase_telemetry", page)
    with pytest.raises(sync.SupabaseSyncError, match="backlog"):
        module.prepare_cloud(store, sync.SupabaseConfig("https://example.invalid", "unused"))
    assert len(calls) == 20


@pytest.mark.skipif(os.name != "nt", reason="Native Windows scheduler definition")
def test_native_schedule_plan_is_disabled_bounded_and_has_no_credentials():
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts" / "reporting-schedule.ps1"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"& '{script}' -Action Plan | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["Interval"] == "PT30M"
    assert plan["EndBoundary"] == "2026-12-07T00:00:00+08:00"
    assert plan["StartWhenAvailable"] and not plan["WakeToRun"]
    assert plan["MultipleInstances"] == "IgnoreNew"
    assert plan["LogonType"] == "Interactive"
    assert plan["RunLevel"] == "Limited"
    assert not plan["Enabled"]
    assert "--allow-one-codex-call" in plan["Arguments"]
    assert "--device-id sim-esp32-03b248" in plan["Arguments"]
    assert "SECRET" not in plan["Arguments"] and "TOKEN" not in plan["Arguments"]


def test_quota_protocol_never_starts_a_thread_or_model_turn(monkeypatch):
    import asyncio

    import energy_monitor.report_worker as module

    sent = []
    class Writer:
        def write(self, line):
            sent.append(json.loads(line))
        async def drain(self):
            pass
    class Process:
        returncode = None
        stdin = Writer()
        def terminate(self):
            self.returncode = 0
        async def wait(self):
            return 0
    async def spawn(*args, **kwargs):
        process = Process()
        process.stdout = asyncio.StreamReader()
        for value in ({"id": 1, "result": {}}, {"id": 2, "result": quota()}):
            process.stdout.feed_data((json.dumps(value) + "\n").encode())
        process.stdout.feed_eof()
        return process
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module.shutil, "which", lambda _: "codex")
    assert module.quota_remaining(module.read_codex_usage()) == (90, 80)
    assert [item["method"] for item in sent] == [
        "initialize", "initialized", "account/rateLimits/read",
    ]


def test_unknown_quota_pauses_ai_but_catches_up_templates(tmp_path):
    from energy_monitor.report_worker import run_reporting_pass

    store, _ = seeded_cycles(tmp_path)
    result = run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        usage_reader=lambda: {}, runner=lambda _: pytest.fail("Unknown quota invoked AI"),
    )
    assert result["status"] == "usage_unavailable"
    assert result["templates_created"] == 1
    assert len(store.fetch_reports()) == 1


def test_worker_does_not_start_ai_if_sync_crosses_project_cutoff(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    import energy_monitor.report_worker as module

    current = [module.STOP_AT - timedelta(seconds=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]
    monkeypatch.setattr(module, "datetime", Clock)
    store, _ = seeded_cycles(tmp_path)
    result = module.run_reporting_pass(
        store, profile_id="tefal-kettle", device_id="monitor-001", state_dir=tmp_path / "worker",
        prepare=lambda: current.__setitem__(0, module.STOP_AT), usage_reader=quota,
        runner=lambda _: pytest.fail("AI started after cutoff"),
    )
    assert result["status"] == "expired"
    assert not list((tmp_path / "worker" / "attempts").glob("*.json"))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows scheduler safeguards")
def test_scheduler_rejects_drifted_safety_settings_without_registration():
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts" / "reporting-schedule.ps1"
    command = f". '{script}' -Action Plan | Out-Null\n" + r'''
    $safe = [pscustomobject]@{
        Principal = [pscustomobject]@{
            UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            LogonType = 'Interactive'; RunLevel = 'Limited'
        }
        Settings = [pscustomobject]@{
            WakeToRun = $false; StartWhenAvailable = $true
            MultipleInstances = 'IgnoreNew'; ExecutionTimeLimit = 'PT10M'
        }
        Triggers = @([pscustomobject]@{
            Repetition = [pscustomobject]@{ Interval = 'PT30M' }
            EndBoundary = '2026-12-07T00:00:00+08:00'
        })
    }
    Assert-ReportingTaskSafety $safe
    $safe.Principal.UserId = $safe.Principal.UserId.Split('\')[-1]
    Assert-ReportingTaskSafety $safe
    $changes = @(
        { param($t) $t.Settings.WakeToRun = $true },
        { param($t) $t.Settings.StartWhenAvailable = $false },
        { param($t) $t.Settings.MultipleInstances = 'Parallel' },
        { param($t) $t.Settings.ExecutionTimeLimit = 'PT0S' },
        { param($t) $t.Principal.UserId = 'someone-else' },
        { param($t) $t.Principal.RunLevel = 'Highest' },
        { param($t) $t.Principal.LogonType = 'Password' },
        { param($t) $t.Triggers[0].Repetition.Interval = 'PT1M' },
        { param($t) $t.Triggers[0].EndBoundary = '' }
    )
    foreach ($change in $changes) {
        $drifted = $safe | ConvertTo-Json -Depth 8 | ConvertFrom-Json
        & $change $drifted
        $rejected = $false
        try { Assert-ReportingTaskSafety $drifted } catch { $rejected = $true }
        if (-not $rejected) { throw 'Unsafe task accepted' }
    }
    Write-Output $changes.Count
    '''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9"
