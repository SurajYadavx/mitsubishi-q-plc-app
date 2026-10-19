/*
 * plc_monitor.ino
 * ───────────────
 * NodeMCU ESP8266 firmware: Serial JSON → MQTT/TLS bridge.
 *
 * Role in the system:
 *   1. Receive newline-delimited JSON from the Windows gateway over USB serial.
 *   2. Validate the JSON structure.
 *   3. Publish validated readings to a managed MQTT broker over TLS (port 8883).
 *   4. Publish periodic online/availability status.
 *   5. Send a Last-Will "offline" message if the connection drops unexpectedly.
 *
 * This firmware has NO capability to write to the PLC or control outputs.
 *
 * Libraries required (install via Arduino Library Manager):
 *   - ArduinoJson    ≥ 7.x   (Benoit Blanchon)
 *   - PubSubClient   ≥ 2.8   (Nick O'Leary)
 *   - ESP8266WiFi    (bundled with ESP8266 Arduino core ≥ 3.1)
 *   - WiFiClientSecure (bundled with ESP8266 Arduino core)
 *
 * Board config:
 *   Board: NodeMCU 1.0 (ESP-12E Module)
 *   Upload speed: 115200
 *   CPU: 80 MHz
 *   Flash size: 4MB (FS:2MB OTA:~1019KB)
 *
 * Credentials:
 *   All Wi-Fi and MQTT credentials are in secrets.h (NOT committed to Git).
 *   Copy secrets.h.example → secrets.h and fill in your values.
 */

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "secrets.h"   // WIFI_SSID, WIFI_PASSWORD, MQTT_HOST, MQTT_PORT,
                        // MQTT_USER, MQTT_PASS, MQTT_CLIENT_ID

// ─── Topic structure ──────────────────────────────────────────────────────────
// plcmon/{gateway_id}/telemetry   ← PLC readings
// plcmon/{gateway_id}/status      ← device availability
// plcmon/{gateway_id}/diag        ← gateway diagnostics / errors

#define TOPIC_BASE        "plcmon/plc-gw-01"
#define TOPIC_TELEMETRY   TOPIC_BASE "/telemetry"
#define TOPIC_STATUS      TOPIC_BASE "/status"
#define TOPIC_DIAG        TOPIC_BASE "/diag"

// ─── Hardware / tunables ──────────────────────────────────────────────────────
#define SERIAL_BAUD       115200
#define STATUS_INTERVAL_MS 10000   // publish online status every 10 s
#define WIFI_TIMEOUT_MS    15000   // give up Wi-Fi attempt after 15 s
#define MQTT_KEEPALIVE_S   30

// Serial line buffer
static const int BUF_SIZE = 1024;
static char serialBuf[BUF_SIZE];
static int  bufPos = 0;

// Timing
static unsigned long lastStatusMs = 0;

// MQTT + TLS
WiFiClientSecure  wifiClient;
PubSubClient      mqttClient(wifiClient);

// ─────────────────────────────────────────────────────────────────────────────
// Wi-Fi
// ─────────────────────────────────────────────────────────────────────────────
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  // Bug fix: the original implementation used a blocking delay(500) loop for
  // up to WIFI_TIMEOUT_MS (15 s), freezing the serial receiver during reconnect.
  // Now this function is non-blocking: it initiates (or re-initiates) the
  // connection attempt on each call and returns immediately. The loop() will
  // call it again on the next iteration if still not connected.
  static unsigned long wifiAttemptStart = 0;
  static bool wifiAttempting = false;

  if (!wifiAttempting) {
    Serial.println("[WiFi] Connecting…");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiAttemptStart = millis();
    wifiAttempting = true;
    return;
  }

  // Check timeout
  if (millis() - wifiAttemptStart > WIFI_TIMEOUT_MS) {
    Serial.println("[WiFi] Timeout – will retry");
    WiFi.disconnect();
    wifiAttempting = false;  // reset so next call restarts
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] Connected, IP: ");
    Serial.println(WiFi.localIP());
    wifiAttempting = false;
  }
  // else: still waiting – return and let loop() come back
}

// ─────────────────────────────────────────────────────────────────────────────
// MQTT
// ─────────────────────────────────────────────────────────────────────────────
void connectMQTT() {
  if (mqttClient.connected()) return;
  if (WiFi.status() != WL_CONNECTED) return;

  Serial.print("[MQTT] Connecting to ");
  Serial.print(MQTT_HOST);
  Serial.print(":");
  Serial.println(MQTT_PORT);

  // Last-Will: marks device offline if TCP drops without a clean disconnect
  const char* willMsg = "{\"status\":\"offline\",\"device\":\"" MQTT_CLIENT_ID "\"}";

  bool ok = mqttClient.connect(
    MQTT_CLIENT_ID,
    MQTT_USER,
    MQTT_PASS,
    TOPIC_STATUS,       // LWT topic
    1,                  // LWT QoS
    true,               // LWT retain
    willMsg             // LWT payload
  );

  if (ok) {
    Serial.println("[MQTT] Connected");
    // Publish immediate online status
    publishStatus("online");
  } else {
    Serial.print("[MQTT] Failed, rc=");
    Serial.println(mqttClient.state());
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Publish helpers
// ─────────────────────────────────────────────────────────────────────────────
void publishStatus(const char* statusVal) {
  // {"status":"online","device":"nodemcu-01"}
  char buf[128];
  snprintf(buf, sizeof(buf),
           "{\"status\":\"%s\",\"device\":\"" MQTT_CLIENT_ID "\"}",
           statusVal);
  mqttClient.publish(TOPIC_STATUS, buf, /*retain=*/true);
}

void publishTelemetry(const char* jsonLine) {
  mqttClient.publish(TOPIC_TELEMETRY, jsonLine, /*retain=*/false);
}

void publishDiag(const char* msg) {
  char buf[256];
  // Bug fix: use %s in snprintf to avoid format-string injection if 'msg'
  // contains format specifiers from a malformed gateway error string.
  snprintf(buf, sizeof(buf),
           "{\"device\":\"" MQTT_CLIENT_ID "\",\"diag\":\"%s\"}", msg);
  mqttClient.publish(TOPIC_DIAG, buf, /*retain=*/false);
}

// ─────────────────────────────────────────────────────────────────────────────
// JSON validation
// ─────────────────────────────────────────────────────────────────────────────
/*
 * Validate that the line from the gateway contains at minimum:
 *   { "type": <string>, "gw_id": <string>, "seq": <number>, "ts": <string> }
 *
 * Returns true if valid, false otherwise.
 * On success, fills outDoc with the parsed document.
 */
bool validateReading(const char* line, JsonDocument& outDoc) {
  DeserializationError err = deserializeJson(outDoc, line);
  if (err) {
    Serial.print("[JSON] Parse error: ");
    Serial.println(err.c_str());
    return false;
  }

  // Mandatory fields
  if (!outDoc["type"].is<const char*>()) { Serial.println("[JSON] Missing 'type'"); return false; }
  if (!outDoc["gw_id"].is<const char*>()) { Serial.println("[JSON] Missing 'gw_id'"); return false; }
  if (!outDoc["seq"].is<int>())            { Serial.println("[JSON] Missing 'seq'");   return false; }
  if (!outDoc["ts"].is<const char*>())     { Serial.println("[JSON] Missing 'ts'");    return false; }

  return true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Serial line accumulator
// ─────────────────────────────────────────────────────────────────────────────
/*
 * Reads bytes from Serial1 (or Serial if the gateway is on the same USB line).
 * Returns true and fills 'line' when a complete newline-terminated line is ready.
 *
 * NOTE: The gateway uses USB-serial (the same physical USB cable used for
 *       flashing).  On NodeMCU:
 *         • Serial  (UART0) = USB via CH340 chip  ← gateway data comes in here
 *         • Serial1 (UART1) = GPIO2, TX only      ← available for debug output
 *
 * We use Serial for gateway data and duplicate debug prints on Serial (they
 * appear on the same monitor but are easy to distinguish by prefix).
 */
bool readSerialLine(char* line, int maxLen) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (bufPos > 0) {
        serialBuf[bufPos] = '\0';
        strncpy(line, serialBuf, maxLen - 1);
        line[maxLen - 1] = '\0';
        bufPos = 0;
        return true;
      }
      // Empty line – ignore
    } else {
      if (bufPos < BUF_SIZE - 1) {
        serialBuf[bufPos++] = c;
      } else {
        // Buffer overflow – discard and reset
        Serial.println("[SER] Buffer overflow – discarding line");
        bufPos = 0;
      }
    }
  }
  return false;
}

// ─────────────────────────────────────────────────────────────────────────────
// setup / loop
// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(100);
  Serial.println("\n[SYS] PLC Monitor firmware starting…");

  // TLS: For EMQX Cloud Serverless (port 8883), setInsecure() enables TLS 1.2
  // encryption without requiring root CA certificates in ESP8266 RAM.
  // Note: setInsecure() encrypts traffic in transit but does NOT verify
  // the broker certificate against a local trust anchor.
  wifiClient.setInsecure();

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  // PubSubClient default buffer is 256 bytes. Telemetry JSON (~255B) plus topic (26B)
  // exceeds 256B. Allocate 1024B buffer to prevent packet drops.
  mqttClient.setBufferSize(1024);
  mqttClient.setKeepAlive(MQTT_KEEPALIVE_S);
  // This firmware does NOT subscribe to any command topics (read-only role).

  connectWiFi();
  connectMQTT();
}

void loop() {
  // ── Maintain connections ──────────────────────────────────────────
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  // ── Periodic online status ────────────────────────────────────────
  unsigned long now = millis();
  if (now - lastStatusMs >= STATUS_INTERVAL_MS) {
    lastStatusMs = now;
    if (mqttClient.connected()) {
      publishStatus("online");
    }
  }

  // ── Process incoming serial line ──────────────────────────────────
  static char lineBuf[BUF_SIZE];
  if (readSerialLine(lineBuf, BUF_SIZE)) {
    JsonDocument doc;
    if (validateReading(lineBuf, doc)) {
      const char* msgType = doc["type"];

      if (strcmp(msgType, "plc_reading") == 0 ||
          strcmp(msgType, "gateway_error") == 0) {
        // Forward verbatim to the telemetry topic
        if (mqttClient.connected()) {
          publishTelemetry(lineBuf);
          Serial.print("[MQTT] Published telemetry seq=");
          Serial.println((int)doc["seq"]);
        } else {
          Serial.println("[MQTT] Not connected – telemetry dropped");
        }

        // Mirror gateway errors to diag topic
        if (strcmp(msgType, "gateway_error") == 0) {
          const char* errMsg = doc["error"] | "unknown";
          publishDiag(errMsg);
        }

      } else if (strcmp(msgType, "heartbeat") == 0) {
        // Heartbeat: update status topic only
        if (mqttClient.connected()) {
          publishStatus("online");
        }
      }
      // Unknown types: log and discard
    } else {
      publishDiag("invalid_json_from_gateway");
    }
  }
}
