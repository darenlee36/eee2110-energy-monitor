import { createClient } from "@supabase/supabase-js";

const headers = { "Content-Type": "application/json; charset=utf-8" };
const allowedStates = {
  appliance_state: new Set(["off", "heating", "unknown"]),
  connection_state: new Set(["online", "stale", "offline"]),
  anomaly_status: new Set(["not_evaluated", "normal", "anomaly"]),
  quality_status: new Set([
    "valid",
    "read_error",
    "communication_gap",
    "power_interruption",
  ]),
};

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

function error(status: number, code: string, message: string): Response {
  return response(status, { error: { code, message } });
}

function isNumberInRange(value: unknown, minimum: number, maximum: number): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function isIdentifier(value: unknown): boolean {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{1,64}$/.test(value);
}

function isReading(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const reading = value as Record<string, unknown>;
  return (
    isIdentifier(reading.device_id) &&
    isIdentifier(reading.profile_id) &&
    typeof reading.timestamp === "string" &&
    !Number.isNaN(Date.parse(reading.timestamp)) &&
    Number.isInteger(reading.sample_sequence) &&
    (reading.sample_sequence as number) >= 0 &&
    Number.isInteger(reading.device_uptime_ms) &&
    (reading.device_uptime_ms as number) >= 0 &&
    isNumberInRange(reading.voltage_v, 0, 300) &&
    isNumberInRange(reading.current_a, 0, 100) &&
    isNumberInRange(reading.active_power_w, 0, 25_000) &&
    isNumberInRange(reading.cumulative_energy_kwh, 0, Number.MAX_SAFE_INTEGER) &&
    isNumberInRange(reading.frequency_hz, 40, 70) &&
    isNumberInRange(reading.power_factor, 0, 1) &&
    isNumberInRange(reading.battery_voltage_v, 2.5, 4.5) &&
    allowedStates.appliance_state.has(String(reading.appliance_state)) &&
    allowedStates.connection_state.has(String(reading.connection_state)) &&
    allowedStates.anomaly_status.has(String(reading.anomaly_status)) &&
    allowedStates.quality_status.has(String(reading.quality_status)) &&
    typeof reading.firmware_version === "string" &&
    reading.firmware_version.length >= 1 &&
    reading.firmware_version.length <= 32
  );
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") return error(405, "METHOD_NOT_ALLOWED", "Use POST for ingestion");

  const expectedToken = Deno.env.get("DEVICE_INGEST_TOKEN");
  if (!expectedToken || request.headers.get("x-device-token") !== expectedToken) {
    return error(401, "UNAUTHORIZED", "Device credential is missing or invalid");
  }

  const rawBody = await request.text();
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return error(400, "INVALID_JSON", "Request body must be valid JSON");
  }

  const batchId = payload.batch_id;
  const readings = payload.readings;
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (typeof batchId !== "string" || idempotencyKey !== batchId) {
    return error(422, "IDEMPOTENCY_KEY_MISMATCH", "Idempotency-Key must match batch_id");
  }
  if (!Array.isArray(readings) || readings.length < 1 || readings.length > 6 || !readings.every(isReading)) {
    return error(422, "VALIDATION_ERROR", "Telemetry batch is invalid");
  }
  if (new Set(readings.map((reading) => reading.device_id)).size !== 1) {
    return error(422, "VALIDATION_ERROR", "All readings must belong to one device");
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
  );
  const requestHash = await sha256(rawBody);
  const claim = await supabase.from("ingestion_batches").insert({
    batch_id: batchId,
    request_hash: requestHash,
  });

  if (claim.error) {
    if (claim.error.code !== "23505") return error(500, "STORAGE_ERROR", "Could not claim batch");
    const existing = await supabase
      .from("ingestion_batches")
      .select("request_hash,state,accepted,duplicates")
      .eq("batch_id", batchId)
      .single();
    if (existing.error) return error(500, "STORAGE_ERROR", "Could not read batch state");
    if (existing.data.request_hash !== requestHash) {
      return error(422, "IDEMPOTENCY_CONFLICT", "batch_id was reused with a different body");
    }
    if (existing.data.state !== "completed") {
      return error(409, "BATCH_IN_PROGRESS", "The original batch is still being processed");
    }
    return response(200, {
      data: {
        batch_id: batchId,
        accepted: existing.data.accepted,
        duplicates: existing.data.duplicates,
        replayed: true,
      },
    });
  }

  const rows = readings.map((reading) => ({
    device_id: reading.device_id,
    profile_id: reading.profile_id,
    recorded_at: reading.timestamp,
    sample_sequence: reading.sample_sequence,
    device_uptime_ms: reading.device_uptime_ms,
    voltage_v: reading.voltage_v,
    current_a: reading.current_a,
    active_power_w: reading.active_power_w,
    cumulative_energy_kwh: reading.cumulative_energy_kwh,
    frequency_hz: reading.frequency_hz,
    power_factor: reading.power_factor,
    appliance_state: reading.appliance_state,
    battery_voltage_v: reading.battery_voltage_v,
    connection_state: reading.connection_state,
    anomaly_status: reading.anomaly_status,
    quality_status: reading.quality_status,
    firmware_version: reading.firmware_version,
  }));
  const inserted = await supabase
    .from("telemetry")
    .upsert(rows, { onConflict: "device_id,sample_sequence", ignoreDuplicates: true })
    .select("id");
  if (inserted.error) {
    await supabase.from("ingestion_batches").update({ state: "failed" }).eq("batch_id", batchId);
    return error(500, "STORAGE_ERROR", "Telemetry could not be stored");
  }

  const accepted = inserted.data.length;
  const duplicates = readings.length - accepted;
  await supabase
    .from("ingestion_batches")
    .update({ state: "completed", accepted, duplicates, completed_at: new Date().toISOString() })
    .eq("batch_id", batchId);

  return response(201, { data: { batch_id: batchId, accepted, duplicates, replayed: false } });
});

