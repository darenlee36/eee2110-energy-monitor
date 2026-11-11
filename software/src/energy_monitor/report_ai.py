"""Hosted AI interpretation backed by an immutable numerical report."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from statistics import fmean

import httpx
from pydantic import BaseModel, ConfigDict, Field

from energy_monitor.reporting import ReportDraft, validate_report

MODEL = "gpt-6-luna"
VERSION = "report-insights-v1"


class AnalysisError(ValueError):
    """A safe error that the dashboard can show to an owner."""


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    findings: list[str] = Field(min_length=1, max_length=3)
    explanations: list[str] = Field(min_length=1, max_length=2)
    actions: list[str] = Field(min_length=1, max_length=3)


def analysis_report(report: dict | ReportDraft) -> ReportDraft:
    source = validate_report(report)
    cycles = source.evidence_snapshot["cycles"]
    if source.engine != "template" or source.status != "generated" or any(
        cycle["data_origin"] != "sensor" or cycle["record_source"] != "measured"
        or cycle["status"] != "completed" for cycle in cycles
    ):
        raise AnalysisError("Select a saved numerical report containing genuine sensor cycles.")
    if not any(cycle["eligible_for_totals"] for cycle in cycles):
        raise AnalysisError("This report has no cycles eligible for numerical comparison.")
    return source


def analysis_evidence(source: ReportDraft, assessment: dict | None) -> dict:
    evidence = source.evidence_snapshot
    cycles = evidence["cycles"]
    included = [(i, c) for i, c in enumerate(cycles, 1) if c["eligible_for_totals"]]
    total = evidence["totals_by_source"]["sensor"]
    duration = fmean(c["duration_seconds"] for _, c in included)
    power = fmean(c["average_power_w"] for _, c in included)
    energy = float(total["energy_kwh"])
    cost = (
        "Estimated charge unavailable." if total["charge_low_rm"] is None
        else f"Estimated charge RM {float(total['charge_low_rm']):.3f}"
             f"–{float(total['charge_high_rm']):.3f}."
    )
    if source.kind == "cycle":
        peak = cycles[0]["peak_power_w"]
        overview = (
            f"The cycle ran for {duration:g} s, used {energy:.5f} kWh and drew "
            f"{power:,.0f} W on average, peaking at {peak:,.0f} W. {cost}"
        )
    else:
        overview = (
            f"{len(included)} of {source.cycle_count} cycles contribute to the averages. "
            f"Mean duration was {duration:.1f} s, mean active power {power:,.0f} W and mean "
            f"energy {energy / len(included):.5f} kWh per cycle. "
            f"Total energy was {energy:.5f} kWh. {cost}"
        )
    findings = {"overview": overview}
    explanations = {
        "conditions": (
            "Water volume and starting temperature can affect heating time. "
            "Use recorded operating conditions to check that possibility."
        ),
    }
    actions = {
        "matched_run": "Compare a repeat run with the same water volume and starting temperature.",
        "log_conditions": "Record water volume and starting temperature on the next run.",
    }
    if len(included) > 1:
        longest, longest_cycle = max(included, key=lambda pair: pair[1]["duration_seconds"])
        shortest, shortest_cycle = min(included, key=lambda pair: pair[1]["duration_seconds"])
        low_power = min(c["average_power_w"] for _, c in included)
        high_power = max(c["average_power_w"] for _, c in included)
        findings["spread"] = (
            f"Mean active power ranged from {low_power:,.0f} to {high_power:,.0f} W. "
            f"Heating duration ranged from {shortest_cycle['duration_seconds']} to "
            f"{longest_cycle['duration_seconds']} s."
        )
        if longest != shortest:
            actions["compare_extremes"] = (
                f"Compare the operating notes for entries {shortest} and {longest}, "
                "the shortest and longest cycles in this report."
            )
    if source.cycle_count > len(included):
        findings["quality"] = (
            f"{source.cycle_count - len(included)} cycles failed the saved quality checks."
        )
        explanations["quality"] = "Missing or invalid samples limit comparisons for excluded runs."
        actions["quality"] = "Review the saved readings of excluded runs before repeating them."
    anomaly = "Comparable anomaly scores were unavailable for this report."
    if source.kind == "cycle" and assessment:
        anomaly = assessment["summary"]
        findings["baseline"] = assessment["comparison"]
        if assessment["pattern"] in ("unusual", "warning"):
            explanations["unusual"] = (
                "Differences in load or starting conditions could contribute to the recorded "
                "deviation. The score alone cannot establish the cause."
            )
    return {
        "overview": overview, "anomaly": anomaly,
        "choices": {"findings": findings, "explanations": explanations, "actions": actions},
    }


def select_insights(evidence: dict, api_key: str) -> tuple[Selection, dict | None]:
    choices = evidence["choices"]
    schema = {
        "type": "object", "required": list(choices), "additionalProperties": False,
        "properties": {
            group: {"type": "array", "items": {"type": "string", "enum": list(options)}}
            for group, options in choices.items()
        },
    }
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL, "store": False, "reasoning": {"effort": "none"},
                "max_output_tokens": 350, "tools": [],
                "instructions": (
                    "Select the most useful evidence-backed findings, possible explanations, "
                    "and actions for this appliance report. Return only candidate IDs. Prefer "
                    "cycle-specific comparisons over repeating the overview. Choose up to "
                    "three findings and actions and up to two explanations. Do not repeat IDs."
                ),
                "input": json.dumps(evidence, allow_nan=False),
                "text": {"format": {
                    "type": "json_schema", "name": "cycle_insights", "strict": True,
                    "schema": schema,
                }},
            },
            timeout=httpx.Timeout(18, connect=5),
        )
        response.raise_for_status()
        result = response.json()
        if result["status"] != "completed" or any(
            item["type"] not in ("message", "reasoning") for item in result["output"]
        ):
            raise ValueError("incomplete or unexpected output")
        parts = [part for item in result["output"] if item["type"] == "message"
                 for part in item["content"]]
        if len(parts) != 1 or parts[0]["type"] != "output_text":
            raise ValueError("structured text output required")
        selection = Selection.model_validate_json(parts[0]["text"])
        for group, ids in selection.model_dump().items():
            if len(ids) != len(set(ids)) or any(item not in choices[group] for item in ids):
                raise ValueError("unsupported evidence reference")
    except httpx.HTTPStatusError as exc:
        reason = {
            401: "OpenAI rejected the API credential.",
            403: "This API project cannot use the selected model.",
            429: "OpenAI reported a usage or rate limit.",
        }.get(exc.response.status_code, "OpenAI could not complete this request.")
        raise AnalysisError(reason) from None
    except (httpx.RequestError, KeyError, TypeError, ValueError):
        raise AnalysisError("OpenAI did not return a usable analysis.") from None
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return selection, None
    counts = {key: usage.get(key) for key in ("input_tokens", "output_tokens")}
    return selection, counts if all(type(v) is int and v >= 0 for v in counts.values()) else None


def analysis_identity(source: ReportDraft) -> dict:
    return {
        "source_id": str(source.report_id), "source_hash": source.evidence_hash,
        "model_name": MODEL, "prompt_version": VERSION,
    }


def generate_analysis(store, source: ReportDraft, assessment: dict | None, api_key: str) -> dict:
    if not store.can_edit or not api_key:
        raise AnalysisError("Owner access and hosted AI configuration are required.")
    source = analysis_report(source)
    identity = analysis_identity(source)
    existing = store._rpc("read_report_ai_analysis", identity)
    if existing is not None:
        return existing
    evidence = analysis_evidence(source, assessment)
    reservation = store._rpc("reserve_report_ai_analysis", identity)
    if reservation != "reserved":
        raise AnalysisError({
            "daily_limit": "The daily AI analysis limit has been reached.",
            "existing": "This report already has a generation attempt. Reopen it to check.",
        }.get(reservation, "Could not reserve this report for analysis."))
    try:
        selection, usage = select_insights(evidence, api_key)
    except AnalysisError as exc:
        store._rpc("finish_report_ai_analysis", {
            **identity, "result": {"status": "error", "error": str(exc)},
        })
        raise
    groups = {
        group: [evidence["choices"][group][key] for key in ids]
        for group, ids in selection.model_dump().items()
    }
    payload = {
        "status": "validated", "overview": evidence["overview"],
        "anomaly": evidence["anomaly"], "sections": groups,
        "selection": selection.model_dump(), "generated_at": datetime.now(UTC).isoformat(),
        "usage": usage,
    }
    if store._rpc("finish_report_ai_analysis", {**identity, "result": payload}) is not True:
        raise AnalysisError("The analysis was generated but its save was not confirmed.")
    return payload
