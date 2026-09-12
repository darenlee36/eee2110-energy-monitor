# Cycle-First Household Kettle Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic telemetry-window dashboard with an automatically detected, persistent, cycle-first household-kettle dashboard using official, versioned TNB tariff components and the approved neon-wave visual system.

**Architecture:** Keep raw telemetry immutable. A pure Python detector converts ordered five-second readings into deterministic cycle summaries; SQLite persists those summaries locally and a Supabase migration mirrors the schema. A versioned tariff service calculates a clearly qualified gross cycle energy charge, while Streamlit reads prepared live/cycle data and preserves interaction state across a five-second fragment refresh.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, Supabase PostgreSQL, Streamlit 1.48+, Plotly 6, pytest, Ruff, PowerShell, CSS.

**Spec:** `docs/superpowers/specs/2026-09-12-cycle-first-kettle-dashboard-design.md`

## Global Constraints

- Run Python commands from `software/` using `.\.venv\Scripts\python.exe`.
- Keep the five-second sample interval and six-reading/thirty-second ingestion batches.
- Use backend power-and-quality rules as the authoritative cycle detector; firmware `appliance_state` is diagnostic only.
- Use deterministic simulation defaults: start at 1,000 W for two consecutive valid readings; stop below 100 W for three consecutive valid readings; warning gap over 15 s; terminating gap over 60 s; minimum duration 30 s; maximum duration 10 min; energy cross-check tolerance 20%.
- Treat raw telemetry as the source of truth and make cycle processing idempotent.
- Use `TNB Domestic General` RP4 components effective 1 July 2025 through 31 December 2027: energy 27.03 sen/kWh up to 1,500 kWh monthly or 37.03 sen/kWh above 1,500 kWh; capacity 4.55 sen/kWh; network 12.85 sen/kWh; retail RM10/month, excluded from per-cycle allocation.
- Seed September 2026 AFA as a 3.67 sen/kWh surcharge; customers at or below 600 kWh monthly are exempt. Never use an AFA forecast as an actual rate.
- When household monthly usage is unknown, return a partial gross-charge range and identify AFA, efficiency incentive, retail charge, tax, fund contributions, and other household-bill rules as excluded or unresolved.
- Never expose a free-form RM/kWh tariff input in the normal dashboard.
- Manual volume labels are `0.5 L`, `1.0 L`, `1.5 L`, or `Unknown`; a manual label always wins over a prediction.
- Do not implement the volume classifier, Isolation Forest, agentic reports, chat, appliance control, real UART mapping, mains wiring, or unsafe hardware instructions in this plan.
- Describe unusual behaviour as a difference from the recorded baseline, never as a confirmed physical fault.
- Recreate the Pinterest neon-wave visual language with original local CSS only; do not download or embed the reference artwork.
- Preserve unrelated worktree files and stage only the files named in each task.

## File Structure

### New files

- `software/src/energy_monitor/cycles.py` — pure detection state machine, deterministic cycle IDs, energy/statistics summarisation, and quality evaluation.
- `software/src/energy_monitor/tariffs.py` — tariff catalog parsing, effective-version selection, gross cycle-charge calculation, and provenance.
- `software/src/energy_monitor/dashboard_queries.py` — read models for Live and Cycles modes; no Streamlit calls.
- `software/config/tariffs/tnb-domestic-general-rp4.json` — versioned official tariff and AFA data with source metadata.
- `software/dashboard/components.py` — focused Streamlit render helpers for command bar, live state, cycle receipt, chart, filters, disclosures, and volume labelling.
- `software/.streamlit/config.toml` — supported Streamlit dark-theme defaults.
- `software/tests/test_cycles.py` — detector and summariser unit tests.
- `software/tests/test_tariffs.py` — tariff selection, calculation, qualification, and provenance tests.
- `software/tests/test_dashboard_queries.py` — live/cycle presentation-model tests.

### Modified files

- `software/src/energy_monitor/models.py` — cycle, volume, quality, and tariff-facing Pydantic types.
- `software/src/energy_monitor/storage.py` — SQLite migrations and cycle/label/tariff repository methods.
- `software/src/energy_monitor/api.py` — invoke cycle processing after successful non-replayed ingestion.
- `software/src/energy_monitor/simulator.py` — deterministic idle/normal/incomplete cycle scenarios.
- `software/dashboard/app.py` — page orchestration and five-second fragment; remove viewport energy and arbitrary tariff controls.
- `software/dashboard/styles.css` — original neon-wave background, glass surfaces, focus, responsive, and reduced-motion styles.
- `software/supabase/migrations/202609120001_initial_schema.sql` — cloud cycle/config/label/tariff tables, indexes, constraints, and RLS.
- `software/tests/test_storage.py` — SQLite persistence, idempotency, label audit, and query coverage.
- `software/tests/test_api.py` — ingestion-to-cycle-processing integration coverage.
- `software/tests/test_simulator.py` — deterministic scenario coverage.
- `software/tests/test_dashboard.py` — Streamlit empty/live/cycle/refresh-copy coverage.
- `software/README.md` — cycle-first run instructions, tariff qualification, and hardware boundary.

---

### Task 1: Add cycle and volume domain contracts

**Files:**
- Modify: `software/src/energy_monitor/models.py`
- Test: `software/tests/test_models.py`

**Interfaces:**
- Consumes: existing `TelemetryReading`, `QualityStatus`, and timezone-aware timestamps.
- Produces: `CycleStatus`, `CycleAssessment`, `VolumeClass`, `CycleDetectionSettings`, and `CycleSummary` for Tasks 2–10.

- [ ] **Step 1: Write failing model tests**

Append tests that lock the allowed values and simulation defaults:

```python
from energy_monitor.models import (
    CycleAssessment,
    CycleDetectionSettings,
    CycleStatus,
    VolumeClass,
)


def test_cycle_detection_settings_use_approved_simulation_defaults() -> None:
    settings = CycleDetectionSettings.simulation_defaults()

    assert settings.version == "sim-cycle-v1"
    assert settings.start_power_w == 1000.0
    assert settings.start_confirm_samples == 2
    assert settings.stop_power_w == 100.0
    assert settings.stop_confirm_samples == 3
    assert settings.warning_gap_seconds == 15
    assert settings.terminating_gap_seconds == 60
    assert settings.minimum_duration_seconds == 30
    assert settings.maximum_duration_seconds == 600
    assert settings.energy_difference_tolerance == 0.20


def test_cycle_and_volume_enums_have_only_approved_values() -> None:
    assert {item.value for item in CycleStatus} == {"active", "completed", "incomplete"}
    assert {item.value for item in CycleAssessment} == {
        "not_evaluated", "normal", "unusual", "insufficient_data"
    }
    assert {item.value for item in VolumeClass} == {
        "0.5_l", "1.0_l", "1.5_l", "unknown"
    }
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```

Expected: collection fails because the new types do not exist.

- [ ] **Step 3: Add the domain types**

Add the following contracts to `models.py`; keep `extra="forbid"` and non-negative constraints:

```python
class CycleStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class CycleAssessment(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    NORMAL = "normal"
    UNUSUAL = "unusual"
    INSUFFICIENT_DATA = "insufficient_data"


class VolumeClass(StrEnum):
    HALF_LITRE = "0.5_l"
    ONE_LITRE = "1.0_l"
    ONE_AND_HALF_LITRES = "1.5_l"
    UNKNOWN = "unknown"


class CycleDetectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    start_power_w: float = Field(gt=0)
    start_confirm_samples: int = Field(ge=1)
    stop_power_w: float = Field(ge=0)
    stop_confirm_samples: int = Field(ge=1)
    warning_gap_seconds: int = Field(gt=0)
    terminating_gap_seconds: int = Field(gt=0)
    minimum_duration_seconds: int = Field(gt=0)
    maximum_duration_seconds: int = Field(gt=0)
    energy_difference_tolerance: float = Field(gt=0, le=1)

    @classmethod
    def simulation_defaults(cls) -> "CycleDetectionSettings":
        return cls(
            version="sim-cycle-v1",
            start_power_w=1000.0,
            start_confirm_samples=2,
            stop_power_w=100.0,
            stop_confirm_samples=3,
            warning_gap_seconds=15,
            terminating_gap_seconds=60,
            minimum_duration_seconds=30,
            maximum_duration_seconds=600,
            energy_difference_tolerance=0.20,
        )
```

Define `CycleSummary` with exact persisted fields:

```python
class CycleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle_id: UUID
    device_id: str
    profile_id: str
    started_at: datetime
    ended_at: datetime | None
    start_sequence: int = Field(ge=0)
    end_sequence: int | None = Field(default=None, ge=0)
    status: CycleStatus
    assessment: CycleAssessment
    duration_seconds: int = Field(ge=0)
    meter_energy_kwh: float = Field(ge=0)
    integrated_energy_kwh: float = Field(ge=0)
    energy_difference_ratio: float = Field(ge=0)
    average_power_w: float = Field(ge=0)
    peak_power_w: float = Field(ge=0)
    average_voltage_v: float = Field(ge=0)
    minimum_voltage_v: float = Field(ge=0)
    maximum_voltage_v: float = Field(ge=0)
    average_current_a: float = Field(ge=0)
    average_power_factor: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=0)
    valid_sample_count: int = Field(ge=0)
    invalid_sample_count: int = Field(ge=0)
    missing_sample_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    largest_gap_seconds: int = Field(ge=0)
    quality_note: str
    detection_version: str
    predicted_volume: VolumeClass | None = None
    prediction_confidence: float | None = Field(default=None, ge=0, le=1)
    volume_model_version: str | None = None
    effective_volume: VolumeClass = VolumeClass.UNKNOWN
```

Add validators requiring timezone-aware cycle timestamps, `ended_at` for non-active cycles, `end_sequence >= start_sequence`, `maximum_duration_seconds > minimum_duration_seconds`, and `terminating_gap_seconds > warning_gap_seconds`.

- [ ] **Step 4: Run model tests and lint**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/models.py tests/test_models.py --no-cache
```

Expected: both commands pass.

- [ ] **Step 5: Commit the contracts**

```powershell
git add src/energy_monitor/models.py tests/test_models.py
git commit -m "feat: define kettle cycle contracts"
```

---

### Task 2: Implement the pure cycle detector and summariser

**Files:**
- Create: `software/src/energy_monitor/cycles.py`
- Create: `software/tests/test_cycles.py`

**Interfaces:**
- Consumes: `detect_cycles(readings: Sequence[TelemetryReading], settings: CycleDetectionSettings) -> list[CycleSummary]`.
- Produces: deterministic `CycleSummary` values; `cycle_uuid(device_id: str, profile_id: str, start_sequence: int) -> UUID`.

- [ ] **Step 1: Write failing happy-path and spike tests**

Create helpers that build telemetry with exact five-second timestamps, cumulative energy, and quality flags. Lock the main behaviour:

```python
def test_detect_cycles_groups_one_boiling_period_into_one_cycle() -> None:
    readings = scenario_readings(idle=3, heating=30, trailing_idle=3, power_w=2000.0)

    cycles = detect_cycles(readings, CycleDetectionSettings.simulation_defaults())

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.start_sequence == 4
    assert cycle.end_sequence == 33
    assert cycle.duration_seconds == 145
    assert cycle.sample_count == 30
    assert cycle.peak_power_w == 2000.0
    assert cycle.meter_energy_kwh == pytest.approx(0.083333, abs=1e-6)


def test_detect_cycles_rejects_one_sample_power_spike() -> None:
    readings = scenario_readings(idle=3, heating=1, trailing_idle=4, power_w=2000.0)

    assert detect_cycles(readings, CycleDetectionSettings.simulation_defaults()) == []
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cycles.py -v
```

Expected: import fails because `energy_monitor.cycles` does not exist.

- [ ] **Step 3: Implement deterministic segmentation**

Implement a single-pass state machine. Use only valid samples for confirmation, retain invalid samples inside an active boundary, and derive the cycle UUID from its stable identity:

```python
CYCLE_NAMESPACE = UUID("7f5795e2-fd31-4cf1-8c68-093061951230")


def cycle_uuid(device_id: str, profile_id: str, start_sequence: int) -> UUID:
    identity = f"{device_id}:{profile_id}:{start_sequence}"
    return uuid5(CYCLE_NAMESPACE, identity)


def detect_cycles(
    readings: Sequence[TelemetryReading],
    settings: CycleDetectionSettings,
) -> list[CycleSummary]:
    ordered = sorted(readings, key=lambda item: (item.timestamp, item.sample_sequence))
    cycles: list[CycleSummary] = []
    candidate_start: list[TelemetryReading] = []
    active: list[TelemetryReading] = []
    candidate_stop: list[TelemetryReading] = []
    idle_predecessor: TelemetryReading | None = None
    cycle_predecessor: TelemetryReading | None = None

    for reading in ordered:
        previous = active[-1] if active else idle_predecessor
        gap_seconds = (
            int((reading.timestamp - previous.timestamp).total_seconds())
            if previous is not None else 0
        )
        if active and gap_seconds > settings.terminating_gap_seconds:
            cycles.append(summarize_cycle(
                active, cycle_predecessor, settings,
                CycleStatus.INCOMPLETE, "Telemetry gap exceeded 60 seconds",
                terminal_gap_seconds=gap_seconds,
            ))
            active = []
            candidate_stop = []

        if reading.quality_status is not QualityStatus.VALID:
            if active:
                active.append(reading)
            continue

        if not active:
            if reading.active_power_w >= settings.start_power_w:
                candidate_start.append(reading)
            else:
                candidate_start = []
                idle_predecessor = reading
            if len(candidate_start) == settings.start_confirm_samples:
                active = candidate_start.copy()
                candidate_start = []
                cycle_predecessor = idle_predecessor
            continue

        active.append(reading)
        active_seconds = int((active[-1].timestamp - active[0].timestamp).total_seconds())
        if active_seconds > settings.maximum_duration_seconds:
            cycles.append(summarize_cycle(
                active, cycle_predecessor, settings,
                CycleStatus.INCOMPLETE, "Cycle exceeded 600 seconds",
            ))
            active = []
            candidate_stop = []
            continue
        if reading.active_power_w < settings.stop_power_w:
            candidate_stop.append(reading)
        else:
            candidate_stop = []
        if len(candidate_stop) == settings.stop_confirm_samples:
            heating = active[:-settings.stop_confirm_samples]
            duration = int((heating[-1].timestamp - heating[0].timestamp).total_seconds())
            if duration >= settings.minimum_duration_seconds:
                cycles.append(summarize_cycle(
                    heating, cycle_predecessor, settings,
                    CycleStatus.COMPLETED, "Cycle completed",
                ))
            idle_predecessor = candidate_stop[-1]
            active = []
            candidate_stop = []

    if active:
        cycles.append(summarize_cycle(
            active, cycle_predecessor, settings,
            CycleStatus.ACTIVE, "Cycle is still heating",
        ))
    return cycles
```

The implementation must use focused helpers: `_is_valid(reading: TelemetryReading) -> bool`, `summarize_cycle(samples: Sequence[TelemetryReading], predecessor: TelemetryReading | None, settings: CycleDetectionSettings, status: CycleStatus, quality_note: str, terminal_gap_seconds: int = 0) -> CycleSummary`, `_meter_energy_kwh(samples: Sequence[TelemetryReading], predecessor: TelemetryReading | None) -> float`, and `_integrated_energy_kwh(samples: Sequence[TelemetryReading]) -> float`. `terminal_gap_seconds` participates in `largest_gap_seconds` and missing-sample counts but its closing reading does not enter the cycle’s power or energy statistics.

For meter energy, include the positive cumulative delta from the valid predecessor to the first heating sample, then each positive in-cycle delta. For integrated energy, use trapezoidal integration over timestamps. `energy_difference_ratio` is `abs(meter-integrated) / meter`, or `0` when both are zero. A completed cycle with duration below 30 seconds is discarded. Active and incomplete cycles use `NOT_EVALUATED` and `INSUFFICIENT_DATA` respectively.

- [ ] **Step 4: Add edge-case tests and make them pass**

Add explicit tests with these exact assertions:

```python
def test_long_gap_closes_active_cycle_as_incomplete() -> None:
    cycle = detect_cycles(incomplete_gap_readings(), SETTINGS)[0]
    assert cycle.status is CycleStatus.INCOMPLETE
    assert cycle.assessment is CycleAssessment.INSUFFICIENT_DATA
    assert cycle.largest_gap_seconds == 65


def test_cumulative_meter_reset_never_creates_negative_energy() -> None:
    cycle = detect_cycles(readings_with_energy_values([1.0, 1.01, 0.001, 0.011]), SETTINGS)[0]
    assert cycle.meter_energy_kwh == pytest.approx(0.02)


def test_cycle_id_is_stable_when_same_readings_are_processed_twice() -> None:
    readings = complete_cycle_readings()
    assert detect_cycles(readings, SETTINGS)[0].cycle_id == detect_cycles(readings, SETTINGS)[0].cycle_id


def test_energy_cross_check_over_twenty_percent_sets_quality_note() -> None:
    cycle = detect_cycles(readings_with_energy_disagreement(), SETTINGS)[0]
    assert cycle.energy_difference_ratio > 0.20
    assert "Energy cross-check differs" in cycle.quality_note
```

Also assert that a 10-second gap reports zero missing readings, a 20-second gap reports three missing readings while remaining completed, invalid samples do not count toward consecutive start/stop confirmation, and 605 seconds of heating closes as `INCOMPLETE`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cycles.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/cycles.py tests/test_cycles.py --no-cache
```

Expected: all detector tests and lint pass.

- [ ] **Step 5: Commit the detector**

```powershell
git add src/energy_monitor/cycles.py tests/test_cycles.py
git commit -m "feat: detect and summarize kettle cycles"
```

---

### Task 3: Persist cycle versions, summaries, and manual labels in SQLite

**Files:**
- Modify: `software/src/energy_monitor/storage.py`
- Modify: `software/tests/test_storage.py`

**Interfaces:**
- Consumes: `CycleDetectionSettings`, `CycleSummary`, `VolumeClass`, and `detect_cycles`.
- Produces: repository methods `process_cycles`, `fetch_cycles`, `fetch_cycle`, `fetch_cycle_readings`, `save_volume_label`, `fetch_effective_volume`, and `fetch_volume_label_history`.

- [ ] **Step 1: Write failing schema and processing tests**

Add tests that insert a complete scenario and process it twice:

```python
def test_processing_cycles_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycles.db")
    store.insert_batch(TelemetryBatch(batch_id=uuid4(), readings=complete_cycle_readings()))
    settings = CycleDetectionSettings.simulation_defaults()

    first = store.process_cycles(settings)
    second = store.process_cycles(settings)

    assert first == second
    assert len(store.fetch_cycles()) == 1
    assert store.fetch_cycles()[0]["status"] == "completed"


def test_manual_volume_correction_preserves_audit_history(tmp_path: Path) -> None:
    store, cycle_id = store_with_completed_cycle(tmp_path)

    store.save_volume_label(cycle_id, VolumeClass.ONE_LITRE)
    store.save_volume_label(cycle_id, VolumeClass.HALF_LITRE)

    assert store.fetch_effective_volume(cycle_id) == VolumeClass.HALF_LITRE
    assert [row["volume_class"] for row in store.fetch_volume_label_history(cycle_id)] == [
        "1.0_l", "0.5_l"
    ]
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_storage.py -v
```

Expected: tests fail because the cycle tables and repository methods are missing.

- [ ] **Step 3: Add focused SQLite tables and indexes**

Extend `_initialize()` with:

```sql
CREATE TABLE IF NOT EXISTS cycle_detection_versions (
    version TEXT PRIMARY KEY,
    settings_json TEXT NOT NULL,
    calibration_status TEXT NOT NULL
        CHECK (calibration_status IN ('simulation_default', 'hardware_validated')),
    effective_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appliance_cycles (
    cycle_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    start_sequence INTEGER NOT NULL,
    end_sequence INTEGER,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'incomplete')),
    assessment TEXT NOT NULL CHECK (
        assessment IN ('not_evaluated', 'normal', 'unusual', 'insufficient_data')
    ),
    summary_json TEXT NOT NULL,
    detection_version TEXT NOT NULL REFERENCES cycle_detection_versions(version),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device_id, profile_id, start_sequence)
);

CREATE INDEX IF NOT EXISTS appliance_cycles_started_at_idx
    ON appliance_cycles(started_at DESC);

CREATE TABLE IF NOT EXISTS cycle_volume_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL REFERENCES appliance_cycles(cycle_id),
    volume_class TEXT NOT NULL CHECK (volume_class IN ('0.5_l', '1.0_l', '1.5_l', 'unknown')),
    source TEXT NOT NULL CHECK (source = 'dashboard'),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_volume_label_per_cycle_idx
    ON cycle_volume_labels(cycle_id) WHERE is_active = 1;
```

- [ ] **Step 4: Implement repository methods and pass tests**

Implement these exact signatures: `process_cycles(self, settings: CycleDetectionSettings) -> list[CycleSummary]`; `fetch_cycles(self, status: str | None = None, volume: str | None = None, limit: int = 100) -> list[dict[str, object]]`; `fetch_cycle(self, cycle_id: UUID | str) -> dict[str, object] | None`; `fetch_cycle_readings(self, cycle_id: UUID | str) -> list[dict[str, object]]`; `save_volume_label(self, cycle_id: UUID | str, volume: VolumeClass) -> None`; `fetch_effective_volume(self, cycle_id: UUID | str) -> VolumeClass`; and `fetch_volume_label_history(self, cycle_id: UUID | str) -> list[dict[str, object]]`.

`process_cycles()` must upsert the immutable settings row, fetch telemetry in full order, call `detect_cycles`, and upsert each deterministic cycle. It must preserve volume-label rows. `save_volume_label()` must start one transaction, deactivate the old label, insert the new active label with `source='dashboard'`, and roll back on failure.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_storage.py tests/test_cycles.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/storage.py tests/test_storage.py --no-cache
```

Expected: storage, cycle, and lint checks pass.

- [ ] **Step 5: Commit SQLite cycle persistence**

```powershell
git add src/energy_monitor/storage.py tests/test_storage.py
git commit -m "feat: persist cycle summaries and volume labels"
```

---

### Task 4: Process cycles after ingestion and make simulator scenarios deterministic

**Files:**
- Modify: `software/src/energy_monitor/api.py`
- Modify: `software/src/energy_monitor/simulator.py`
- Modify: `software/tests/test_api.py`
- Modify: `software/tests/test_simulator.py`

**Interfaces:**
- Consumes: `SQLiteTelemetryStore.process_cycles(CycleDetectionSettings.simulation_defaults())`.
- Produces: ingestion-to-cycle persistence and `generate_cycle_scenario` fixtures for integration/dashboard tests.

- [ ] **Step 1: Write a failing ingestion-to-cycle test**

Add a test server that retains its store, post multiple six-reading batches, and assert that one complete boil becomes one stored cycle:

```python
def test_api_processes_one_cycle_spanning_multiple_batches(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycle-api.db")
    server, base_url = start_test_server_with_store(store)
    readings = generate_cycle_scenario("normal", datetime(2026, 9, 12, 6, 0, tzinfo=UTC))
    try:
        for batch in batch_readings(readings):
            response = httpx.post(
                f"{base_url}/api/v1/telemetry/batches",
                headers={"Idempotency-Key": str(batch.batch_id)},
                json=batch.model_dump(mode="json"),
            )
            assert response.status_code == 201
    finally:
        server.shutdown()
        server.server_close()

    cycles = store.fetch_cycles()
    assert len(cycles) == 1
    assert cycles[0]["status"] == "completed"
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_api_processes_one_cycle_spanning_multiple_batches -v
```

Expected: failure because ingestion does not invoke cycle processing and the scenario helper is absent.

- [ ] **Step 3: Add deterministic simulator scenarios**

Add:

```python
ScenarioName = Literal["normal", "incomplete_gap", "short_spike"]


def generate_cycle_scenario(name: ScenarioName, start_at: datetime) -> list[TelemetryReading]:
    """Return deterministic idle/heating/idle telemetry at five-second intervals."""
```

Use these exact shapes:

- `normal`: 3 idle + 30 readings at 2,050 W + 3 idle.
- `incomplete_gap`: 3 idle + 12 readings at 2,050 W, then move the next timestamp forward by 65 seconds and finish with 3 idle.
- `short_spike`: 3 idle + 1 reading at 2,050 W + 4 idle.

Keep the existing seeded `generate_readings()` for backwards-compatible free-running demonstrations, but change the CLI default to `--scenario normal`; allow `--scenario stream` to use the existing generator.

- [ ] **Step 4: Invoke processing after committed ingestion and pass tests**

In `api.py`, after `store.insert_batch(batch)` succeeds:

```python
if not result.replayed:
    store.process_cycles(CycleDetectionSettings.simulation_defaults())
```

Do not process malformed, rejected, conflicting, or replayed batches. Add tests proving a replay keeps one cycle and an incomplete-gap scenario stores `incomplete`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_simulator.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/api.py src/energy_monitor/simulator.py tests/test_api.py tests/test_simulator.py --no-cache
```

Expected: API/simulator tests and lint pass.

- [ ] **Step 5: Commit the processing flow**

```powershell
git add src/energy_monitor/api.py src/energy_monitor/simulator.py tests/test_api.py tests/test_simulator.py
git commit -m "feat: process cycles after telemetry ingestion"
```

---

### Task 5: Add official versioned TNB tariff calculation

**Files:**
- Create: `software/config/tariffs/tnb-domestic-general-rp4.json`
- Create: `software/src/energy_monitor/tariffs.py`
- Create: `software/tests/test_tariffs.py`
- Modify: `software/src/energy_monitor/models.py`

**Interfaces:**
- Consumes: cycle energy, cycle date, and optional monthly household usage.
- Produces: `load_tariff_catalog(path) -> TariffCatalog` and `estimate_cycle_charge(energy_kwh, occurred_at, monthly_household_kwh, catalog) -> TariffEstimate`.

- [ ] **Step 1: Write failing tariff tests**

Create exact tests for official components, September AFA, exemption, high-use energy rate, and unknown usage:

```python
CATALOG_PATH = Path(__file__).parents[1] / "config" / "tariffs" / "tnb-domestic-general-rp4.json"


def test_known_300_kwh_household_uses_base_components_and_afa_exemption() -> None:
    estimate = estimate_cycle_charge(
        energy_kwh=Decimal("0.087"),
        occurred_at=datetime(2026, 9, 12, tzinfo=UTC),
        monthly_household_kwh=Decimal("300"),
        catalog=load_tariff_catalog(CATALOG_PATH),
    )

    assert estimate.energy_rate_sen_per_kwh == Decimal("27.03")
    assert estimate.capacity_rate_sen_per_kwh == Decimal("4.55")
    assert estimate.network_rate_sen_per_kwh == Decimal("12.85")
    assert estimate.afa_rate_sen_per_kwh == Decimal("0")
    assert estimate.gross_variable_rate_sen_per_kwh == Decimal("44.43")
    assert estimate.amount_rm == Decimal("0.0387")
    assert estimate.status == TariffEstimateStatus.PARTIAL
    assert "energy_efficiency_incentive" in estimate.excluded_components


def test_800_kwh_household_applies_september_2026_afa() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"), datetime(2026, 9, 12, tzinfo=UTC), Decimal("800"),
        load_tariff_catalog(CATALOG_PATH),
    )
    assert estimate.afa_rate_sen_per_kwh == Decimal("3.67")
    assert estimate.gross_variable_rate_sen_per_kwh == Decimal("48.10")
    assert estimate.amount_rm == Decimal("0.0418")


def test_unknown_household_usage_returns_qualified_rate_range() -> None:
    estimate = estimate_cycle_charge(
        Decimal("0.087"), datetime(2026, 9, 12, tzinfo=UTC), None,
        load_tariff_catalog(CATALOG_PATH),
    )
    assert estimate.status == TariffEstimateStatus.PARTIAL
    assert estimate.amount_rm is None
    assert estimate.amount_range_rm == (Decimal("0.0387"), Decimal("0.0474"))
    assert "afa_eligibility" in estimate.unresolved_components
```

The unknown-use upper bound is the 54.43 sen/kWh high-use base rate; AFA is unresolved and therefore is not silently added to either bound.

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tariffs.py -v
```

Expected: import/file failure because the tariff module and catalog do not exist.

- [ ] **Step 3: Add the source-controlled official tariff catalog**

Create the JSON with decimal strings, not binary floats:

```json
{
  "provider": "TNB",
  "scheme": "Domestic General",
  "currency": "MYR",
  "tariff_versions": [
    {
      "version": "tnb-domestic-general-rp4-2025-07",
      "effective_from": "2025-07-01",
      "effective_to": "2027-12-31",
      "energy_rate_up_to_1500_sen_per_kwh": "27.03",
      "energy_rate_above_1500_sen_per_kwh": "37.03",
      "capacity_rate_sen_per_kwh": "4.55",
      "network_rate_sen_per_kwh": "12.85",
      "retail_charge_rm_per_month": "10.00",
      "retail_waiver_max_monthly_kwh": "600",
      "source_url": "https://myenergystats.st.gov.my/documents/d/guest/tariff-tnb-pdf-1",
      "source_published_date": "2025-06-20",
      "last_checked_date": "2026-09-12"
    }
  ],
  "afa_periods": [
    {
      "version": "tnb-afa-2026-09",
      "month": "2026-09",
      "rate_sen_per_kwh": "3.67",
      "exempt_max_monthly_kwh": "600",
      "source_url": "https://www.singlebuyer.com.my/Generation-Info-Tariff/automatic-fuel-adjustment-(afa)",
      "last_checked_date": "2026-09-12"
    }
  ]
}
```

- [ ] **Step 4: Implement Decimal-based calculation and pass tests**

Add Pydantic models/enums for `TariffEstimateStatus` and `TariffEstimate`, then implement:

```python
def load_tariff_catalog(path: Path) -> TariffCatalog:
    return TariffCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def estimate_cycle_charge(
    energy_kwh: Decimal,
    occurred_at: datetime,
    monthly_household_kwh: Decimal | None,
    catalog: TariffCatalog,
) -> TariffEstimate:
    # Select tariff by occurred_at date and AFA by YYYY-MM.
    # Use Decimal throughout and ROUND_HALF_UP to RM 0.0001.
    # Never include retail or complete-bill components in the cycle amount.
```

Rules:

- `monthly_household_kwh <= 1500`: energy rate 27.03.
- `monthly_household_kwh > 1500`: energy rate 37.03.
- AFA is zero when usage is at or below 600; otherwise use the exact month’s published actual AFA.
- Missing AFA above 600 returns `UNAVAILABLE` for a single amount but may retain base-component detail.
- Unknown monthly use returns the base-rate range and unresolved AFA eligibility.
- Always exclude retail charge, energy-efficiency incentive, taxes, fund contributions, rebates, and complete-bill rounding.
- `PARTIAL` is mandatory while the energy-efficiency incentive is not implemented.
- A timestamp outside the effective tariff period returns `UNAVAILABLE`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tariffs.py tests/test_models.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/tariffs.py src/energy_monitor/models.py tests/test_tariffs.py --no-cache
```

Expected: tariff tests and lint pass.

- [ ] **Step 5: Commit tariff calculation**

```powershell
git add config/tariffs/tnb-domestic-general-rp4.json src/energy_monitor/tariffs.py src/energy_monitor/models.py tests/test_tariffs.py
git commit -m "feat: calculate versioned TNB cycle charges"
```

---

### Task 6: Add dashboard read models and cycle comparisons

**Files:**
- Create: `software/src/energy_monitor/dashboard_queries.py`
- Create: `software/tests/test_dashboard_queries.py`
- Modify: `software/src/energy_monitor/storage.py`

**Interfaces:**
- Consumes: storage rows, cycle summaries, tariff catalog, optional `ENERGY_MONITOR_MONTHLY_KWH`.
- Produces: `LiveView`, `CycleListItem`, `CycleDetail`, `get_live_view`, `list_cycle_items`, and `get_cycle_detail`.

- [ ] **Step 1: Write failing presentation-model tests**

```python
def test_live_view_distinguishes_instantaneous_values_from_cycle_totals(tmp_path: Path) -> None:
    store = populated_store(tmp_path, scenario="normal")
    view = get_live_view(store, now=datetime(2026, 9, 12, 6, 5, tzinfo=UTC))

    assert view.state in {"idle", "heating", "cycle_complete"}
    assert view.latest_power_w >= 0
    assert view.last_cycle is not None
    assert view.last_cycle.energy_label == "Measured cycle energy"


def test_cycle_detail_compares_only_matching_effective_volume(tmp_path: Path) -> None:
    store, selected_id = store_with_labelled_cycles(tmp_path)
    detail = get_cycle_detail(store, selected_id, tariff_context=None)

    assert detail.effective_volume == VolumeClass.ONE_LITRE
    assert detail.comparison_text == "12 s longer than the median of your valid 1.0 L cycles"
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard_queries.py -v
```

Expected: import fails because the query module does not exist.

- [ ] **Step 3: Implement focused read models**

Define immutable Pydantic models with already formatted semantic fields, while keeping numeric fields for charts:

```python
class LiveView(BaseModel):
    state: Literal["empty", "idle", "heating", "cycle_complete"]
    connection_state: ConnectionState
    updated_at: datetime | None
    latest_power_w: float | None
    latest_voltage_v: float | None
    latest_current_a: float | None
    elapsed_seconds: int | None
    energy_so_far_kwh: float | None
    charge_so_far: TariffEstimate | None
    quality_note: str
    active_cycle: CycleSummary | None
    last_cycle: CycleListItem | None


class CycleListItem(BaseModel):
    cycle_id: UUID
    started_at: datetime
    duration_seconds: int
    energy_kwh: float
    charge_text: str
    effective_volume: VolumeClass
    assessment: CycleAssessment
    status: CycleStatus


class CycleDetail(BaseModel):
    summary: CycleSummary
    readings: list[TelemetryReading]
    tariff_estimate: TariffEstimate
    effective_volume: VolumeClass
    manual_volume: VolumeClass | None
    comparison_text: str
    chart_summary: str
```

Implement these exact query signatures: `get_live_view(store: SQLiteTelemetryStore, now: datetime) -> LiveView`; `list_cycle_items(store: SQLiteTelemetryStore, status_filter: str = "all", volume_filter: str = "all", limit: int = 100) -> list[CycleListItem]`; and `get_cycle_detail(store: SQLiteTelemetryStore, cycle_id: UUID | str, tariff_context: Decimal | None) -> CycleDetail`.

- [ ] **Step 4: Implement same-volume deterministic comparison and pass tests**

Only completed, valid, matching-effective-volume cycles enter the median. Exclude the selected cycle. Require at least three comparator cycles; otherwise return `Not enough similar labelled cycles for comparison`. Use integer seconds and absolute wording:

```python
def comparison_text(selected_seconds: int, peer_seconds: Sequence[int], volume: VolumeClass) -> str:
    if volume is VolumeClass.UNKNOWN or len(peer_seconds) < 3:
        return "Not enough similar labelled cycles for comparison"
    difference = selected_seconds - round(median(peer_seconds))
    direction = "longer" if difference > 0 else "shorter"
    if difference == 0:
        return f"Matches the median of your valid {display_volume(volume)} cycles"
    return f"{abs(difference)} s {direction} than the median of your valid {display_volume(volume)} cycles"
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard_queries.py tests/test_storage.py tests/test_tariffs.py -v
.\.venv\Scripts\python.exe -m ruff check src/energy_monitor/dashboard_queries.py tests/test_dashboard_queries.py --no-cache
```

Expected: query, storage, tariff, and lint checks pass.

- [ ] **Step 5: Commit query services**

```powershell
git add src/energy_monitor/dashboard_queries.py src/energy_monitor/storage.py tests/test_dashboard_queries.py
git commit -m "feat: prepare live and cycle dashboard views"
```

---

### Task 7: Mirror the cycle/tariff schema in Supabase securely

**Files:**
- Modify: `software/supabase/migrations/202609120001_initial_schema.sql`

**Interfaces:**
- Consumes: field/status names from Tasks 1, 3, and 5.
- Produces: PostgreSQL tables compatible with local cycle, label, detection-version, tariff-version, AFA, and estimate records.

- [ ] **Step 1: Add a failing migration-contract test**

Extend `tests/test_contract_schema.py` to assert the migration contains exact tables, checks, and RLS statements:

```python
def test_supabase_migration_contains_cycle_first_tables_and_rls() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    for table in (
        "cycle_detection_versions", "appliance_cycles", "cycle_volume_labels",
        "tariff_versions", "afa_periods", "cycle_cost_estimates",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
    assert "unique (device_id, profile_id, start_sequence)" in sql
    assert "one_active_volume_label_per_cycle" in sql
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_contract_schema.py -v
```

Expected: failure because the migration has only profiles, batches, and telemetry.

- [ ] **Step 3: Add PostgreSQL tables and constraints**

Add normalized tables matching the SQLite/domain names. Use `jsonb` for settings and summary payloads, `numeric(12,6)` for energy, `numeric(8,4)` for RM amounts, UUID cycle IDs, and `timestamptz` timestamps. Add:

```sql
constraint appliance_cycles_device_start_unique
    unique (device_id, profile_id, start_sequence)
```

For manual labels, create the partial unique index:

```sql
create unique index if not exists one_active_volume_label_per_cycle
    on public.cycle_volume_labels(cycle_id)
    where is_active;
```

Insert `sim-cycle-v1`, the RP4 tariff version, and September 2026 AFA with `on conflict do nothing`. Preserve decimal values exactly.

- [ ] **Step 4: Add least-privilege RLS and pass contract tests**

Enable RLS on every new table. Grant authenticated users read access to cycle/config/tariff/estimate rows. Permit label insertion/update only through a future narrowly scoped server function; do not grant general `insert`, `update`, or `delete` on telemetry, cycles, tariff records, or cost estimates. Revoke direct access to processing metadata from `anon`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_contract_schema.py -v
.\.venv\Scripts\python.exe -m ruff check tests/test_contract_schema.py --no-cache
```

If Supabase CLI is installed, additionally run from `software/`:

```powershell
supabase db lint --local
```

Expected: Python contract tests pass. If the CLI is unavailable, record that PostgreSQL execution remains pending rather than claiming it ran.

- [ ] **Step 5: Commit the Supabase schema**

```powershell
git add supabase/migrations/202609120001_initial_schema.sql tests/test_contract_schema.py
git commit -m "feat: add cycle-first Supabase schema"
```

---

### Task 8: Build the Live/Cycles Streamlit interaction model

**Files:**
- Create: `software/dashboard/components.py`
- Modify: `software/dashboard/app.py`
- Modify: `software/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `get_live_view`, `list_cycle_items`, `get_cycle_detail`, storage label methods, and tariff context.
- Produces: a state-preserving Streamlit page with `Live` and `Cycles` modes, refresh controls, history selection, cycle receipt/chart, disclosures, and volume labelling.

- [ ] **Step 1: Replace old expectations with failing cycle-first dashboard tests**

Add/adjust AppTest checks:

```python
def test_dashboard_empty_state_has_refresh_controls_and_no_fake_metrics(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "empty.db"))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Kettle cycle monitor"
    assert any(button.label == "Refresh now" for button in app.button)
    assert "No telemetry" in app.info[0].value
    assert "Energy in view" not in [metric.label for metric in app.metric]
    assert "Estimated cost" not in [metric.label for metric in app.metric]


def test_completed_cycle_renders_one_cycle_receipt(tmp_path: Path, monkeypatch: object) -> None:
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=10).run()

    assert not app.exception
    assert "Measured cycle energy" in [metric.label for metric in app.metric]
    assert "Estimated gross cycle charge" in [metric.label for metric in app.metric]
    assert all("Development tariff rate" not in item.label for item in app.number_input)
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v
```

Expected: old page title, tariff input, and viewport metrics cause failures.

- [ ] **Step 3: Create focused rendering helpers**

In `components.py`, implement these exact interfaces: `render_command_bar(connection: ConnectionState, updated_at: datetime | None) -> str`; `render_live_state(view: LiveView) -> None`; `render_cycle_filters() -> tuple[str, str]`; `render_cycle_history(items: list[CycleListItem]) -> UUID | None`; `render_cycle_receipt(detail: CycleDetail) -> None`; `build_cycle_figure(detail: CycleDetail, start_threshold_w: float) -> go.Figure`; `render_tariff_disclosure(estimate: TariffEstimate) -> None`; and `render_volume_label(store: SQLiteTelemetryStore, cycle_id: UUID, current: VolumeClass) -> None`.

Use `st.segmented_control` for `Live | Cycles`, `st.toggle` for auto-refresh, `st.button("Refresh now", type="primary")`, a dataframe configured with `on_select="rerun"` and `selection_mode="single-row"` for desktop history, and `st.pills` for volumes. Use `st.session_state` keys:

```python
DEFAULT_SESSION_STATE = {
    "mode": "Live",
    "auto_refresh": True,
    "selected_cycle_id": None,
    "status_filter": "All",
    "volume_filter": "All volumes",
}
```

- [ ] **Step 4: Rebuild page orchestration and pass tests**

`app.py` must:

1. Set page title `Kettle cycle monitor` and load local CSS.
2. Initialise session state without overwriting existing choices.
3. Open `SQLiteTelemetryStore` from `ENERGY_MONITOR_DB`.
4. Read optional `ENERGY_MONITOR_MONTHLY_KWH`; parse to `Decimal` or leave `None`.
5. Render the persistent command bar.
6. Put only the live query/render inside a fragment whose `run_every` is `5s` when enabled and `None` when paused.
7. Make **Refresh now** call `st.rerun(scope="fragment")` inside the live fragment or `st.rerun()` from the page-level control, according to Streamlit scope rules.
8. Keep history selection/filter state outside the live fragment.
9. Default to the newest completed cycle only when `selected_cycle_id` is empty.
10. Show a completion status without replacing a selected historical cycle.

Remove the sidebar tariff-rate control, `calculate_energy_used_kwh` viewport use, `calculate_cost_rm`, `Energy in view`, unqualified `Estimated cost`, and primary battery card.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/test_dashboard_queries.py -v
.\.venv\Scripts\python.exe -m ruff check dashboard/app.py dashboard/components.py tests/test_dashboard.py --no-cache
```

Expected: dashboard/query tests and lint pass.

- [ ] **Step 5: Commit the interaction model**

```powershell
git add dashboard/app.py dashboard/components.py tests/test_dashboard.py
git commit -m "feat: add live and cycle dashboard modes"
```

---

### Task 9: Implement the one-cycle Plotly chart, tariff disclosure, and label workflow

**Files:**
- Modify: `software/dashboard/components.py`
- Modify: `software/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `CycleDetail.readings`, `TariffEstimate`, detection threshold, and label repository.
- Produces: accessible cycle chart, calculation details, raw-data disclosure, and auditable manual label interaction.

- [ ] **Step 1: Write failing component-shape tests**

```python
def test_cycle_figure_uses_elapsed_seconds_threshold_and_quality_markers() -> None:
    figure = build_cycle_figure(cycle_detail_with_invalid_sample(), start_threshold_w=1000.0)

    assert figure.layout.xaxis.title.text == "Elapsed time (s)"
    assert figure.layout.yaxis.title.text == "Active power (W)"
    assert figure.layout.hovermode == "x unified"
    assert any(trace.name == "Detection threshold" for trace in figure.data)
    assert any(trace.name == "Invalid/missing sample" for trace in figure.data)


def test_tariff_disclosure_names_provenance_and_exclusions() -> None:
    estimate = known_partial_estimate()
    text = tariff_disclosure_text(estimate)
    assert "TNB Domestic General" in text
    assert "September 2026 AFA" in text
    assert "not a complete household bill" in text
    assert "energy-efficiency incentive" in text
```

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -k "figure or tariff_disclosure" -v
```

Expected: helper output lacks the required traces/text.

- [ ] **Step 3: Complete the Plotly figure**

Build elapsed seconds from the cycle start. Add:

```python
go.Scatter(
    x=elapsed_seconds,
    y=power_w,
    mode="lines",
    name="Selected cycle",
    line={"color": "#3BD7FF", "width": 3},
    fill="tozeroy",
    fillcolor="rgba(226, 45, 255, 0.12)",
    customdata=custom_hover,
    hovertemplate=(
        "%{x:.0f} s<br>%{y:,.0f} W<br>Voltage %{customdata[0]:.1f} V"
        "<br>Current %{customdata[1]:.2f} A<br>Cycle energy %{customdata[2]:.5f} kWh"
        "<extra></extra>"
    ),
)
```

Add a dashed threshold trace, start/end markers, and red diamond markers for non-valid readings. Keep the mode bar hidden and add a nearby `chart_summary` sentence.

- [ ] **Step 4: Complete disclosures and volume save feedback**

The tariff expander must list measured kWh; energy, capacity, network, and AFA rates; calculated/range amount; effective dates; source links; last-checked date; unresolved components; exclusions; and `This is not a complete household bill.`

The label workflow must use `Save label` and `Skip`. On successful save, show `Volume label saved` as a status message. On failure, retain the selected pill in session state, show `Label was not saved`, and render `Retry save`. Correction must create a new audit row rather than update old label history.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v
.\.venv\Scripts\python.exe -m ruff check dashboard/components.py tests/test_dashboard.py --no-cache
```

Expected: dashboard tests and lint pass.

- [ ] **Step 5: Commit chart and disclosures**

```powershell
git add dashboard/components.py tests/test_dashboard.py
git commit -m "feat: add cycle chart tariff details and labels"
```

---

### Task 10: Apply the original neon-wave theme and accessibility states

**Files:**
- Create: `software/.streamlit/config.toml`
- Modify: `software/dashboard/styles.css`
- Modify: `software/dashboard/components.py`

**Interfaces:**
- Consumes: semantic classes emitted by dashboard components.
- Produces: responsive, high-contrast, reduced-motion neon styling without external image assets.

- [ ] **Step 1: Add supported Streamlit dark-theme configuration**

Create:

```toml
[theme]
base = "dark"
primaryColor = "#3BD7FF"
backgroundColor = "#070812"
secondaryBackgroundColor = "#131525"
textColor = "#F7F8FF"
font = "sans-serif"
baseRadius = "large"
showWidgetBorder = true
showSidebarBorder = true
chartCategoricalColors = ["#3BD7FF", "#E22DFF", "#FF4F70", "#FF7448", "#72F6C7"]
```

- [ ] **Step 2: Replace the bland CSS variables and background**

Use original local CSS with no remote URL:

```css
:root {
  --canvas: #070812;
  --surface: rgba(19, 21, 37, 0.82);
  --surface-strong: rgba(24, 27, 48, 0.94);
  --ink: #f7f8ff;
  --muted: #aeb5cb;
  --line: rgba(184, 196, 255, 0.18);
  --cyan: #3bd7ff;
  --violet: #7b4dff;
  --pink: #e22dff;
  --coral: #ff4f70;
  --orange: #ff7448;
  --mint: #72f6c7;
  --warning: #ffc857;
  --danger: #ff5f6d;
}

[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(ellipse 70% 35% at -10% 20%, rgba(59, 215, 255, 0.32), transparent 68%),
    radial-gradient(ellipse 55% 28% at 108% 12%, rgba(226, 45, 255, 0.34), transparent 70%),
    radial-gradient(ellipse 75% 34% at 85% 108%, rgba(255, 79, 112, 0.28), transparent 72%),
    linear-gradient(145deg, #05060c 0%, #090a18 48%, #070812 100%);
  color: var(--ink);
}

[data-testid="stAppViewContainer"]::before {
  content: "";
  position: fixed;
  inset: -20%;
  pointer-events: none;
  background: conic-gradient(
    from 205deg at 42% 55%,
    transparent 0 18%, rgba(59, 215, 255, 0.24) 24%,
    rgba(123, 77, 255, 0.30) 31%, rgba(226, 45, 255, 0.32) 38%,
    rgba(255, 116, 72, 0.28) 45%, transparent 53% 100%
  );
  filter: blur(52px) saturate(125%);
  opacity: 0.78;
}
```

Keep content above the decorative layer and use glass surfaces only where contrast remains compliant.

- [ ] **Step 3: Style semantic states, controls, and focus**

Add explicit classes for `.state-idle`, `.state-heating`, `.state-complete`, `.status-normal`, `.status-warning`, and `.status-unusual`. Pair each with visible text/icon from `components.py`. Add:

```css
:where(button, [role="button"], input, select, textarea):focus-visible {
  outline: 3px solid var(--cyan) !important;
  outline-offset: 3px;
}

.cycle-card,
.state-panel,
.cycle-receipt {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 1.25rem;
  box-shadow: 0 18px 55px rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(18px);
}
```

Ensure primary buttons and pills are at least 44 px high even though the WCAG minimum is 24 px.

- [ ] **Step 4: Add responsive and reduced-motion rules**

```css
@media (max-width: 640px) {
  .block-container { padding: 1rem; }
  .state-panel, .cycle-receipt { border-radius: 1rem; }
  .command-bar { align-items: stretch; flex-direction: column; }
  .primary-measurement { font-size: clamp(2.25rem, 15vw, 4rem); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

Run the Streamlit test and lint suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v
.\.venv\Scripts\python.exe -m ruff check dashboard --no-cache
```

Expected: tests and lint pass; no external asset request is introduced.

- [ ] **Step 5: Commit the visual system**

```powershell
git add .streamlit/config.toml dashboard/styles.css dashboard/components.py
git commit -m "style: apply accessible neon kettle dashboard theme"
```

---

### Task 11: Update documentation and run full verification

**Files:**
- Modify: `software/README.md`
- Modify: `software/scripts/run-simulator.ps1`
- Modify only if required by test discovery: `software/scripts/test-all.ps1`

**Interfaces:**
- Consumes: completed Tasks 1–10.
- Produces: reproducible local instructions and recorded verification evidence.

- [ ] **Step 1: Update run instructions and claims**

Change the README architecture to:

```text
simulated ESP32 readings -> ingestion API -> raw telemetry
    -> automatic cycle processor -> cycle summaries -> Live/Cycles dashboard
```

Document three terminals, the `normal` scenario, automatic cycle processing, volume labels, optional `ENERGY_MONITOR_MONTHLY_KWH`, and tariff qualification. Replace the old RM0.60 statement with:

```text
The dashboard uses versioned official TNB Domestic General RP4 components. It reports a gross variable cycle-energy estimate, not a complete household bill. If monthly household usage is unknown, it shows a qualified range because the applicable energy tier, AFA eligibility, and energy-efficiency incentive cannot be inferred safely.
```

Keep the existing firmware/mains-safety boundary verbatim in meaning.

- [ ] **Step 2: Make the simulator script generate one clear normal cycle**

Update `run-simulator.ps1` so its command invokes:

```powershell
& $python -m energy_monitor.simulator --scenario normal @args
```

The script must retain passed arguments and fail with the simulator’s exit code.

- [ ] **Step 3: Run all automated checks**

From `software/`:

```powershell
.\scripts\test-all.ps1
.\scripts\build-firmware.ps1
```

Expected:

- All Python tests pass.
- Ruff reports no violations.
- Native firmware tests pass.
- ESP32 firmware builds in simulated mode.
- Existing no-GPIO and no-mains-instruction boundaries remain unchanged.

- [ ] **Step 4: Run the local flow and inspect desktop/mobile behaviour**

Start the API, upload a normal scenario, and start Streamlit in separate terminals:

```powershell
.\scripts\start-local-api.ps1
.\scripts\run-simulator.ps1
.\scripts\start-dashboard.ps1
```

Verify in a real browser:

- Empty -> idle/heating/completed content is coherent for available data.
- `Live | Cycles`, auto-refresh, pause/resume, and **Refresh now** work.
- Selecting/filtering cycles survives live refresh.
- One normal cycle spans multiple batches but appears once.
- Chart threshold, start/end, hover, and invalid markers are visible.
- Manual label and correction persist.
- Tariff disclosure shows official components, September 2026 AFA provenance, exclusions, and complete-bill disclaimer.
- Neon ribbons remain behind readable panels and are not downloaded assets.
- Keyboard focus is visible.
- At 320 px and a representative desktop width, primary content does not horizontally scroll.
- Reduced-motion mode has no looping or pulsing effect.

- [ ] **Step 5: Commit documentation and final verified state**

```powershell
git add README.md scripts/run-simulator.ps1 scripts/test-all.ps1
git commit -m "docs: explain cycle-first kettle dashboard"
git status --short
```

Expected: only unrelated pre-existing user files remain untracked/modified. Record exact test counts and any unavailable Supabase CLI validation in the completion report; do not claim checks that did not run.

## Final Definition of Done

- A normal synthetic boil is automatically detected as exactly one completed cycle despite crossing multiple ingestion batches.
- Reprocessing and batch replay do not duplicate the cycle.
- Live power/voltage/current are instantaneous; active energy/charge say `so far`; completed energy/charge are cycle-bound.
- Cycle history, detail, filtering, chart, quality states, and manual volume correction operate correctly.
- The arbitrary RM0.60 input and viewport energy/cost cards are gone.
- Official TNB RP4 components and September 2026 AFA are source-versioned; unknown household context produces a qualified partial/range result.
- The dashboard has five-second auto-refresh, persistent pause, manual refresh, and stable historical selection.
- The original CSS neon-wave background is visually recognisable, responsive, keyboard accessible, and motion-safe.
- Supabase schema mirrors local cycle/tariff records with RLS and least privilege.
- Agentic reporting, ML training, real hardware integration, and mains work remain deferred.
- Full tests, lint, native firmware tests, ESP32 simulated build, and browser verification are reported with fresh evidence.
