"""One guarded laptop pass. Windows schedules this process, never a browser session."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from energy_monitor.report_narrative import codex_environment, codex_review_id, enhance_report
from energy_monitor.reporting import build_report, generate_missing_reports, validate_report
from energy_monitor.storage import SQLiteTelemetryStore

DEFAULT_STATE = Path(__file__).parents[2] / "data" / "reporting-worker"
STOP_AT = datetime(2026, 12, 7, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))


class UsageGuardError(RuntimeError):
    """Pause without recording a failed AI completion or consuming a model invocation."""


def ensure_reporting_window() -> None:
    if datetime.now(UTC) >= STOP_AT:
        raise UsageGuardError("expired")


def codex_executable() -> str | None:
    return os.environ.get("ENERGY_MONITOR_CODEX_EXECUTABLE") or shutil.which("codex")


def quota_remaining(snapshot: dict, *, now: float | None = None) -> tuple[float, float]:
    try:
        if snapshot.get("ordinaryUsageAllowed") is False or snapshot.get("spendControlReached"):
            raise UsageGuardError("usage_limit")
        bucket = (
            snapshot["rateLimitsByLimitId"]["codex"]
            if "rateLimitsByLimitId" in snapshot else snapshot["rateLimits"]
        )
        remaining = []
        for name, duration in (("primary", 300), ("secondary", 10080)):
            window = bucket[name]
            used, reset = window["usedPercent"], window["resetsAt"]
            if (
                type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100
                or type(reset) not in (int, float) or not math.isfinite(reset)
                or reset <= (time.time() if now is None else now)
                or window["windowDurationMins"] != duration
            ):
                raise ValueError("unknown quota window")
            remaining.append(100 - used)
        if min(remaining) <= 5:
            raise UsageGuardError("usage_limit")
        return tuple(remaining)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UsageGuardError("usage_unavailable") from exc


async def _read_usage() -> dict:
    executable = codex_executable()
    if executable is None:
        raise UsageGuardError("usage_unavailable")
    with tempfile.TemporaryDirectory(prefix="energy-quota-") as folder:
        process = await asyncio.create_subprocess_exec(
            executable, "app-server", "--listen", "stdio://", "--disable", "apps",
            "--disable", "plugins", "--disable", "hooks", "--disable", "memories",
            "-c", 'forced_login_method="chatgpt"', cwd=folder, env=codex_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        async def send(value: dict) -> None:
            process.stdin.write((json.dumps(value) + "\n").encode())
            await process.stdin.drain()

        async def response(identity: int) -> dict:
            for _ in range(50):
                line = await process.stdout.readline()
                if not line or len(line) > 64_000:
                    raise UsageGuardError("usage_unavailable")
                value = json.loads(line)
                if value.get("id") == identity:
                    if "error" in value:
                        raise UsageGuardError("usage_unavailable")
                    return value["result"]
                if "id" in value and "method" in value:
                    raise UsageGuardError("usage_unavailable")
            raise UsageGuardError("usage_unavailable")

        try:
            await send({"id": 1, "method": "initialize", "params": {"clientInfo": {
                "name": "energy_monitor_quota", "title": "Energy Monitor quota guard",
                "version": "0.1.0",
            }}})
            await response(1)
            await send({"method": "initialized", "params": {}})
            await send({"id": 2, "method": "account/rateLimits/read"})
            return await response(2)
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()


def read_codex_usage() -> dict:
    async def bounded() -> dict:
        return await asyncio.wait_for(_read_usage(), 20)

    try:
        return asyncio.run(bounded())
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        raise UsageGuardError("usage_unavailable") from exc


@contextmanager
def worker_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (PermissionError, BlockingIOError):
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _save_status(state_dir: Path, result: dict) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    result["checked_at"] = datetime.now(UTC).isoformat()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_dir,
                                     prefix="status-", suffix=".tmp", delete=False) as handle:
        json.dump(result, handle, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, state_dir / "status.json")
    return result


def run_reporting_pass(
    store: SQLiteTelemetryStore, *, profile_id: str, device_id: str, state_dir: Path,
    usage_reader: Callable | None = None, runner: Callable | None = None,
    prepare: Callable | None = None, now: datetime | None = None, report_id: str | None = None,
) -> dict:
    from energy_monitor.report_narrative import run_codex

    result = {"status": "idle", "templates_created": 0, "ai_attempts": 0, "held_attempts": 0}
    if (now or datetime.now(UTC)) >= STOP_AT:
        return _save_status(state_dir, {**result, "status": "expired"})
    with worker_lock(state_dir / "worker.lock") as acquired:
        if not acquired:
            return {**result, "status": "already_running"}
        try:
            if prepare is not None:
                prepare()
            if report_id is not None:
                saved = store.fetch_report(report_id)
                if saved is None:
                    raise ValueError("saved template report not found")
                base = validate_report(saved)
                if base.engine != "template" or (base.profile_id, base.device_id) != (
                    profile_id, device_id
                ):
                    raise ValueError("template report scope differs")
                candidates = [base]
            else:
                result["templates_created"] = generate_missing_reports(
                    store, profile_id=profile_id, device_id=device_id
                )
                candidates = (build_report(
                    store, profile_id=profile_id, device_id=device_id, cycle_id=cycle["cycle_id"]
                ) for cycle in reversed(store.fetch_report_cycles(profile_id, device_id)))
            for base in candidates:
                target = str(codex_review_id(base))
                if store.fetch_report(target) is not None:
                    continue
                attempts = state_dir / "attempts"
                attempts.mkdir(exist_ok=True)
                reservation = attempts / f"{target}.json"
                if reservation.exists():
                    result["held_attempts"] += 1
                    continue
                try:
                    ensure_reporting_window()
                    result["remaining_percent"] = quota_remaining(
                        (usage_reader or read_codex_usage)()
                    )
                    ensure_reporting_window()
                except UsageGuardError as exc:
                    return _save_status(state_dir, {**result, "status": str(exc)})
                # Atomic, durable reservation survives a crash or an unconfirmed cloud save.
                with reservation.open("x", encoding="utf-8") as handle:
                    json.dump({"report_id": target, "started_at": datetime.now(UTC).isoformat()},
                              handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    saved = enhance_report(store, str(base.report_id), runner=runner or run_codex)
                except UsageGuardError as exc:
                    # Only this invocation's own reservation; inference did not start.
                    reservation.unlink()
                    return _save_status(state_dir, {**result, "status": str(exc)})
                outcome = store.fetch_report(saved)
                if outcome is None:
                    raise ValueError("review save unconfirmed")
                result.update(status="completed" if outcome["status"] == "generated"
                              else "review_failed", ai_attempts=1, report_id=saved)
                return _save_status(state_dir, result)
            if result["held_attempts"]:
                result["status"] = "needs_review"
            return _save_status(state_dir, result)
        except Exception:
            _save_status(state_dir, {**result, "status": "error"})
            raise


def prepare_cloud(store: SQLiteTelemetryStore, config) -> None:
    from energy_monitor.supabase_sync import SupabaseSyncError, sync_supabase_telemetry

    for _ in range(20):
        before = store.cloud_sync_cursor(config.url)
        sync_supabase_telemetry(store, config)
        if store.cloud_sync_cursor(config.url) == before:
            store.refresh_edits()
            return
    raise SupabaseSyncError("Cloud backlog continues; next pass will resume the cursor")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    from energy_monitor.cloud_edits import SupabaseCycleStore
    from energy_monitor.supabase_sync import SupabaseConfig, SupabaseSyncError

    parser = argparse.ArgumentParser(description="One quota-guarded laptop reporting pass")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--source", required=True, choices=("local", "supabase"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--allow-one-codex-call", required=True, action="store_true")
    args = parser.parse_args(argv)
    if args.codex_executable is not None:
        if not args.codex_executable.is_absolute() or not args.codex_executable.is_file():
            parser.error("existing absolute Codex executable path required")
        os.environ["ENERGY_MONITOR_CODEX_EXECUTABLE"] = str(args.codex_executable)
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value)
           for value in (args.profile_id, args.device_id)):
        parser.error("invalid device/profile identifier")
    if datetime.now(UTC) >= STOP_AT:
        print(json.dumps(_save_status(args.state_dir, {"status": "expired", "ai_attempts": 0})))
        return 0
    # Only the trusted parent reads cloud credentials; model subprocesses get a minimal env.
    load_dotenv(Path(__file__).parents[2] / ".env")
    try:
        config = SupabaseConfig.from_environment() if args.source == "supabase" else None
        if args.source == "supabase" and config is None:
            raise SupabaseSyncError("Cloud configuration missing")
        store = (SupabaseCycleStore(args.database, config, can_edit=True) if config
                 else SQLiteTelemetryStore(args.database))
        result = run_reporting_pass(
            store, profile_id=args.profile_id, device_id=args.device_id, state_dir=args.state_dir,
            prepare=(lambda: prepare_cloud(store, config)) if config else None,
        )
    except (SupabaseSyncError, OSError, ValueError, sqlite3.Error):
        # Do not print underlying HTTP exceptions, evidence or credential-bearing config.
        print(json.dumps({"status": "error", "message": "Reporting failed; check local status"}))
        return 1
    print(json.dumps(result))
    return 0 if result["status"] in ("completed", "idle", "already_running", "expired") else 1


if __name__ == "__main__":
    from energy_monitor.report_worker import main as run_main

    raise SystemExit(run_main())
