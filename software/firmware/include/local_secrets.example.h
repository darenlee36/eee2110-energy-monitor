#pragma once

// Copy this file to local_secrets.h and replace the values locally.
// local_secrets.h is ignored by Git.
// The service-role key must never be placed on the ESP32.
constexpr char WIFI_SSID[] = "CHANGE_ME";
constexpr char WIFI_PASSWORD[] = "CHANGE_ME";
constexpr char INGEST_URL[] = "https://YOUR_PROJECT.supabase.co/functions/v1/ingest-telemetry";
constexpr char DEVICE_INGEST_TOKEN[] = "CHANGE_ME";
constexpr char DEVICE_ID[] = "monitor-001";
constexpr char PROFILE_ID[] = "tefal-kettle";
