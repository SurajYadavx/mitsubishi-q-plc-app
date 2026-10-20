/*
 * plc_monitor.ino
 * ---------------
 * NodeMCU ESP8266 firmware: Serial JSON -> MQTT/TLS bridge.
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
 *   - ArduinoJson    >= 7.x   (Benoit Blanchon)
 *   - PubSubClient   >= 2.8   (Nick O'Leary)
 *   - ESP8266WiFi    (bundled with ESP8266 Arduino core >= 3.1)
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
 *   Replace the placeholders in secrets.h with your own values.
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <WiFiClientSecure.h>
#include <time.h>

#include "secrets.h" // Wi-Fi and MQTT credentials; optional NTP_SERVER_* values

#if __has_include("ca_cert.h")
#include "ca_cert.h" // BROKER_ROOT_CA (PROGMEM)
#define CA_CERT_HEADER_FOUND 1
#else
#define CA_CERT_HEADER_FOUND 0
static const char BROKER_ROOT_CA[] PROGMEM = "";
#endif

// NTP server defaults (configurable via secrets.h for isolated plants).
#ifndef NTP_SERVER_1
#define NTP_SERVER_1 "pool.ntp.org"
#endif
#ifndef NTP_SERVER_2
#define NTP_SERVER_2 "time.nist.gov"
#endif
#ifndef NTP_SERVER_3
#define NTP_SERVER_3 "time.google.com"
#endif

// Topic structure:
// plcmon/{gateway_id}/telemetry   <- PLC readings
// plcmon/{gateway_id}/status      <- device availability
// plcmon/{gateway_id}/diag        <- gateway diagnostics / errors

#define TOPIC_BASE "plcmon/plc-gw-01"
#define TOPIC_TELEMETRY TOPIC_BASE "/telemetry"
#define TOPIC_STATUS TOPIC_BASE "/status"
#define TOPIC_DIAG TOPIC_BASE "/diag"

// Hardware and tunables.
#define SERIAL_BAUD 115200
#define STATUS_INTERVAL_MS 10000 // publish online status every 10 s
#define WIFI_TIMEOUT_MS 15000    // give up Wi-Fi attempt after 15 s
#define MQTT_KEEPALIVE_S 30
#define MQTT_RETRY_INTERVAL_MS                                                 \
  5000 // Minimum interval between MQTT connection attempts

// Explicit socket timeouts bounding worst-case synchronous publish/connection duration
#define TLS_SOCKET_TIMEOUT_MS 2000 // 2.0s max for BearSSL TLS / TCP socket operations
#define MQTT_SOCKET_TIMEOUT_S 2    // 2s max for PubSubClient socket operations

// Frame validation classification (declared before function prototypes)
enum FrameValidationResult {
  FRAME_VALID = 0,
  FRAME_ERR_PARSE = 1,
  FRAME_ERR_MISSING_ID = 2,
  FRAME_ERR_INVALID_SCHEMA = 3
};

// Serial line buffer
static const int BUF_SIZE = 1024;
static char serialBuf[BUF_SIZE];
static int  bufPos = 0;
static bool discardingUntilNewline = false;
static bool lastLineHadOverrun = false;
static bool lastLineHadRxError = false;

// Stop-and-wait duplicate frame protection state
static char lastCompletedType[32] = {0};
static int  lastCompletedSeq = -1;
static char lastCompletedGwId[32] = {0};
static bool hasCompletedFrame = false;

// Timing
static unsigned long lastStatusMs = 0;
static unsigned long lastMqttAttemptMs = 0;
static bool mqttAttempted = false;

// MQTT + TLS: Global lifetime for BearSSL trust anchor list and clients
static BearSSL::X509List trustedCA;
static bool caConfiguredAndValid = false;

WiFiClientSecure  wifiClient;
PubSubClient      mqttClient(wifiClient);

// Forward declarations for compiler compatibility
void publishStatus(const char* statusVal);
void publishTelemetry(const char* jsonLine);
void publishDiag(const char* msg);
FrameValidationResult validateFrame(const char* line, JsonDocument& outDoc);

// -----------------------------------------------------------------------------
// TLS Trust Anchor & Time Validation Helpers
// -----------------------------------------------------------------------------
/*
 * Flash-safe substring search where BOTH haystack and needle reside in PROGMEM.
 * Reads bytes strictly via pgm_read_byte() to prevent unaligned 8-bit load
 * hardware exceptions (Exception 3) on the Xtensa flash bus.
 */
static bool progmem_contains_P(PGM_P haystack, PGM_P needle) {
  if (!haystack || !needle)
    return false;
  size_t nlen = strlen_P(needle);
  if (nlen == 0)
    return true;
  size_t hlen = strlen_P(haystack);
  if (hlen < nlen)
    return false;
  for (size_t i = 0; i <= hlen - nlen; i++) {
    size_t j = 0;
    for (; j < nlen; j++) {
      if ((char)pgm_read_byte(haystack + i + j) !=
          (char)pgm_read_byte(needle + j)) {
        break;
      }
    }
    if (j == nlen)
      return true;
  }
  return false;
}

/*
 * Verifies that ca_cert.h is present, non-empty, and does not contain the default
 * template placeholder. Security policy strictly fails closed if invalid.
 */
bool isCACertConfigured() {
#if !CA_CERT_HEADER_FOUND
  return false;
#else
  if (strlen_P(BROKER_ROOT_CA) == 0)
    return false;
  if (progmem_contains_P(BROKER_ROOT_CA, PSTR("PASTE_ROOT_CA_CERTIFICATE_HERE")))
    return false;
  if (!progmem_contains_P(BROKER_ROOT_CA, PSTR("-----BEGIN CERTIFICATE-----")))
    return false;
  return true;
#endif
}

/*
 * Non-blocking check for NTP clock synchronization.
 * BearSSL requires accurate time to validate X.509 notBefore and notAfter dates.
 * A timestamp > 1700000000 corresponds to post-November 2023.
 * Logs readable UTC ISO 8601 timestamp and Unix epoch once per synchronization event.
 */
bool isClockSynchronized() {
  time_t now = time(nullptr);
  static bool wasSynchronized = false;

  if (now <= 1700000000) {
    wasSynchronized = false;
    return false;
  }

  if (!wasSynchronized) {
    wasSynchronized = true;
    struct tm timeinfo;
    gmtime_r(&now, &timeinfo);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    Serial.print("[NTP] Clock synchronized: ");
    Serial.print(buf);
    Serial.print(" (epoch=");
    Serial.print((long)now);
    Serial.println(")");
  }

  return true;
}

// -----------------------------------------------------------------------------
// Wi-Fi
// -----------------------------------------------------------------------------
void connectWiFi() {
  static unsigned long wifiAttemptStart = 0;
  static bool wifiAttempting = false;
  static bool wasConnected = false;

  if (WiFi.status() == WL_CONNECTED) {
    if (!wasConnected) {
      wasConnected = true;
      wifiAttempting = false;
      Serial.print("[WiFi] Connected, IP: ");
      Serial.println(WiFi.localIP());

      // Initiate non-blocking background SNTP clock synchronization
      Serial.print("[NTP] Initiating clock sync via: ");
      Serial.print(NTP_SERVER_1);
      Serial.print(", ");
      Serial.println(NTP_SERVER_2);
      configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2, NTP_SERVER_3);
    }
    return;
  }

  // If Wi-Fi dropped after having been connected, reset wasConnected so that
  // when Wi-Fi reconnects, IP and configTime are invoked again.
  wasConnected = false;

  if (!wifiAttempting) {
    Serial.println("[WiFi] Connecting...");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiAttemptStart = millis();
    wifiAttempting = true;
    return;
  }

  // Check timeout
  if (millis() - wifiAttemptStart > WIFI_TIMEOUT_MS) {
    Serial.println("[WiFi] Timeout - will retry");
    WiFi.disconnect();
    wifiAttempting = false; // reset so next call restarts
    return;
  }
  // else: still waiting - return and let loop() come back
}

// -----------------------------------------------------------------------------
// MQTT
// -----------------------------------------------------------------------------
void connectMQTT() {
  if (mqttClient.connected())
    return;
  if (WiFi.status() != WL_CONNECTED)
    return;

  // 1. Enforce fail-closed policy: Abort if Root CA trust anchor is missing or
  // invalid
  if (!caConfiguredAndValid) {
    static unsigned long lastCaAlertMs = 0;
    if (millis() - lastCaAlertMs >= 10000) {
      lastCaAlertMs = millis();
      Serial.println("[TLS] BLOCKED: Cannot connect to MQTT broker without valid Root CA trust anchor.");
      Serial.println(
          "[TLS] Fail-Closed Invariant: Configure ca_cert.h with your "
          "broker's Root CA, then reflash.");
    }
    return;
  }

  // 2. Enforce non-blocking NTP clock synchronization check
  if (!isClockSynchronized()) {
    static unsigned long lastNtpWaitMs = 0;
    static unsigned long ntpWaitStart = 0;
    static unsigned long lastNtpWarnMs = 0;

    if (ntpWaitStart == 0) {
      ntpWaitStart = millis();
    }

    if (millis() - lastNtpWaitMs >= 5000) {
      lastNtpWaitMs = millis();
      time_t now = time(nullptr);
      Serial.print("[NTP] Waiting for clock synchronization before TLS "
                   "handshake... (epoch=");
      Serial.print((long)now);
      Serial.println(")");

      // Rate-limited warning after 30 seconds of waiting without sync
      if (millis() - ntpWaitStart >= 30000) {
        if (lastNtpWarnMs == 0 || millis() - lastNtpWarnMs >= 15000) {
          lastNtpWarnMs = millis();
          Serial.println("[NTP] WARNING: Sync taking >30s. Verify UDP port 123 "
                         "/ DNS is not blocked by network firewall.");
        }
      }
    }
    return; // Non-blocking: loop() continues servicing serial and Wi-Fi
  }

  // Non-blocking retry guard. Unsigned subtraction remains correct across
  // millis() rollover, and the first connection attempt is allowed immediately.
  const unsigned long now = millis();
  if (mqttAttempted &&
      (unsigned long)(now - lastMqttAttemptMs) < MQTT_RETRY_INTERVAL_MS) {
    return;
  }

  Serial.print("[MQTT] Connecting over TLS to ");
  Serial.print(MQTT_HOST);
  Serial.print(":");
  Serial.println(MQTT_PORT);

  // Last-Will: marks device offline if TCP drops without a clean disconnect
  const char *willMsg =
      "{\"status\":\"offline\",\"device\":\"" MQTT_CLIENT_ID "\"}";

  // Record only when we're about to make a real connection attempt.
  lastMqttAttemptMs = millis();
  mqttAttempted = true;
  bool ok = mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS,
                               TOPIC_STATUS, // LWT topic
                               1,            // LWT QoS
                               true,         // LWT retain
                               willMsg       // LWT payload
  );

  if (ok) {
    Serial.println(
        "[MQTT] Connected securely (TLS 1.2 with Root CA validation)");
    // Publish immediate online status
    publishStatus("online");
  } else {
    char sslErrBuf[128] = {0};
    wifiClient.getLastSSLError(sslErrBuf, sizeof(sslErrBuf));
    Serial.print("[MQTT] Connect failed, state=");
    Serial.print(mqttClient.state());
    if (sslErrBuf[0] != '\0') {
      Serial.print(" | SSL Error: ");
      Serial.print(sslErrBuf);
    }
    Serial.println();
  }
}

// -----------------------------------------------------------------------------
// Publish helpers
// -----------------------------------------------------------------------------
void publishStatus(const char* statusVal) {
  // {"status":"online","device":"nodemcu-01"}
  char buf[128];
  snprintf(buf, sizeof(buf),
           "{\"status\":\"%s\",\"device\":\"" MQTT_CLIENT_ID "\"}", statusVal);
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

// -----------------------------------------------------------------------------
// JSON validation
// -----------------------------------------------------------------------------
/*
 * Validate that the line from the gateway contains at minimum:
 *   { "type": <string>, "gw_id": <string>, "seq": <number>, "ts": <string> }
 *
 * Categorizes failure to distinguish unrecoverable parse errors from recoverable schema issues.
 */
FrameValidationResult validateFrame(const char* line, JsonDocument& outDoc) {
  DeserializationError err = deserializeJson(outDoc, line);
  if (err) {
    Serial.print("[JSON] Parse error: ");
    Serial.println(err.c_str());
    return FRAME_ERR_PARSE;
  }

  bool hasType = outDoc["type"].is<const char *>();
  bool hasSeq  = outDoc["seq"].is<int>();
  bool hasGwId = outDoc["gw_id"].is<const char *>();
  bool hasTs   = outDoc["ts"].is<const char *>();

  if (!hasType || !hasSeq) {
    if (!hasType) Serial.println("[JSON] Missing 'type'");
    if (!hasSeq)  Serial.println("[JSON] Missing 'seq'");
    return FRAME_ERR_MISSING_ID;
  }

  if (!hasGwId || !hasTs) {
    if (!hasGwId) Serial.println("[JSON] Missing 'gw_id'");
    if (!hasTs)   Serial.println("[JSON] Missing 'ts'");
    return FRAME_ERR_INVALID_SCHEMA;
  }

  return FRAME_VALID;
}

bool validateReading(const char* line, JsonDocument& outDoc) {
  return validateFrame(line, outDoc) == FRAME_VALID;
}

// -----------------------------------------------------------------------------
// Serial line accumulator
// -----------------------------------------------------------------------------
/*
 * Reads bytes from Serial (USB UART).
 * Returns true and fills 'line' when a complete newline-terminated line is ready.
 *
 * Framing Hardening:
 *   - Accumulates bytes non-blocking until newline (\n or \r)
 *   - Handles CRLF safely and ignores empty lines
 *   - Enforces BUF_SIZE capacity limit
 *   - If overflowed, enters discard-until-newline mode before accepting next frame
 *   - Emits @PROTO_ERR|buffer_overflow upon overflow detection
 */
bool readSerialLine(char* line, int maxLen) {
  // Check hardware UART status indicators supported by ESP8266 core
  if (Serial.hasOverrun()) {
    lastLineHadOverrun = true;
    Serial.println("[SER] UART hardware RX overrun detected (FIFO overflow)");
  }
  if (Serial.hasRxError()) {
    lastLineHadRxError = true;
    Serial.println("[SER] UART hardware RX framing/parity error detected");
  }

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (discardingUntilNewline) {
        discardingUntilNewline = false;
        bufPos = 0;
        lastLineHadOverrun = false;
        lastLineHadRxError = false;
        return false;
      }
      if (bufPos > 0) {
        serialBuf[bufPos] = '\0';
        strncpy(line, serialBuf, maxLen - 1);
        line[maxLen - 1] = '\0';
        bufPos = 0;
        return true;
      }
      // Empty line - ignore
    } else {
      if (discardingUntilNewline) {
        continue;
      }
      if (bufPos < BUF_SIZE - 1) {
        serialBuf[bufPos++] = c;
      } else {
        // Buffer overflow - discard remainder of frame until newline
        discardingUntilNewline = true;
        bufPos = 0;
        Serial.println("@PROTO_ERR|buffer_overflow");
        Serial.println("[SER] Buffer overflow - discarding line until newline");
      }
    }
  }
  return false;
}

// -----------------------------------------------------------------------------
// setup / loop
// -----------------------------------------------------------------------------
void setup() {
  // Configure UART RX buffer to 2048 bytes before Serial.begin() to eliminate
  // hardware FIFO overrun risks during burst transactions or slow TLS routines.
  Serial.setRxBufferSize(2048);
  Serial.begin(SERIAL_BAUD);
  delay(100);
  Serial.println("\n[SYS] PLC Monitor firmware starting...");

  // TLS Root CA trust anchor initialization (strict fail-closed).
  // Note: wifiClient.setInsecure() has been completely removed.
  if (isCACertConfigured()) {
    bool ok = trustedCA.append(BROKER_ROOT_CA);
    if (ok) {
      wifiClient.setTrustAnchors(&trustedCA);
      caConfiguredAndValid = true;
      Serial.println(
          "[TLS] Trusted Root CA installed in BearSSL trust anchors.");
    } else {
      caConfiguredAndValid = false;
      Serial.println("[TLS] CRITICAL: Failed to parse Root CA in ca_cert.h "
                     "(malformed PEM). Failing closed.");
    }
  } else {
    caConfiguredAndValid = false;
    Serial.println("[TLS] CRITICAL: Trusted Root CA certificate is NOT "
                   "configured in ca_cert.h.");
    Serial.println("[TLS] Security policy enforces FAIL-CLOSED: MQTT "
                   "connection will be blocked.");
    Serial.println("[TLS] Configure ca_cert.h with your broker's Root CA "
                   "certificate and reflash.");
  }

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  // PubSubClient default buffer is 256 bytes. Telemetry JSON (~255B) plus topic
  // (26B) exceeds 256B. Allocate 1024B buffer to prevent packet drops.
  mqttClient.setBufferSize(1024);
  mqttClient.setKeepAlive(MQTT_KEEPALIVE_S);
  // Explicitly bound TCP/TLS and MQTT socket timeouts to guarantee synchronous
  // publish/connect execution is bounded within TLS_SOCKET_TIMEOUT_MS (2.0s).
  wifiClient.setTimeout(TLS_SOCKET_TIMEOUT_MS);
  mqttClient.setSocketTimeout(MQTT_SOCKET_TIMEOUT_S);
  // This firmware does NOT subscribe to any command topics (read-only role).

  connectWiFi();
  connectMQTT();
}

void loop() {
  // Maintain connections.
  connectWiFi();
  if (!mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  // Periodic online status.
  unsigned long now = millis();
  if (now - lastStatusMs >= STATUS_INTERVAL_MS) {
    lastStatusMs = now;
    if (mqttClient.connected()) {
      publishStatus("online");
    }
  }

  // Process incoming serial line.
  static char lineBuf[BUF_SIZE];
  if (readSerialLine(lineBuf, BUF_SIZE)) {
    JsonDocument doc;
    FrameValidationResult vres = validateFrame(lineBuf, doc);

    if (vres == FRAME_ERR_PARSE) {
      Serial.println("@PROTO_ERR|malformed_json");
      lastLineHadOverrun = false;
      lastLineHadRxError = false;
      publishDiag("invalid_json_from_gateway");
      return;
    }

    if (vres == FRAME_ERR_MISSING_ID) {
      Serial.println("@PROTO_ERR|missing_identifiers");
      // Safely bounded diagnostic of the rejected line to differentiate transmitted bugs from framing errors
      Serial.print("[SER] Rejected line (len=");
      Serial.print(strlen(lineBuf));
      if (lastLineHadOverrun) {
        Serial.print(", overrun=true");
      }
      if (lastLineHadRxError) {
        Serial.print(", rx_error=true");
      }
      Serial.print("): ");
      Serial.println(lineBuf);
      lastLineHadOverrun = false;
      lastLineHadRxError = false;
      publishDiag("invalid_json_from_gateway");
      return;
    }

    lastLineHadOverrun = false;
    lastLineHadRxError = false;

    if (vres == FRAME_ERR_INVALID_SCHEMA) {
      const char *msgType = doc["type"];
      int seq = (int)doc["seq"];
      Serial.print("@NACK|");
      Serial.print(msgType);
      Serial.print("|");
      Serial.print(seq);
      Serial.println("|invalid_schema");
      publishDiag("invalid_json_from_gateway");
      return;
    }

    // Frame is valid: extract identifiers
    const char *msgType = doc["type"];
    int seq = (int)doc["seq"];
    const char *gwId = doc["gw_id"];

    // ── Stop-and-Wait Duplicate Protection ───────────────────────────────
    if (hasCompletedFrame &&
        lastCompletedSeq == seq &&
        strcmp(lastCompletedType, msgType) == 0 &&
        strcmp(lastCompletedGwId, gwId) == 0) {
      // Retransmission received because previous ACK was lost or delayed.
      // Re-acknowledge without repeating MQTT publish or handler side-effects.
      Serial.print("@ACK|");
      Serial.print(msgType);
      Serial.print("|");
      Serial.println(seq);
      Serial.print("[SER] Duplicate frame suppressed: type=");
      Serial.print(msgType);
      Serial.print(" seq=");
      Serial.println(seq);
      return;
    }

    // ── Synchronous Frame Handler ────────────────────────────────────────
    if (strcmp(msgType, "plc_reading") == 0 ||
        strcmp(msgType, "gateway_error") == 0) {
      // Forward verbatim to the telemetry topic
      if (mqttClient.connected()) {
        publishTelemetry(lineBuf);
        Serial.print("[MQTT] Published telemetry seq=");
        Serial.println(seq);
      } else {
        Serial.println("[MQTT] Not connected - telemetry dropped");
      }

      // Mirror gateway errors to diag topic
      if (strcmp(msgType, "gateway_error") == 0) {
        const char *errMsg = doc["error"] | "unknown";
        publishDiag(errMsg);
      }

    } else if (strcmp(msgType, "heartbeat") == 0) {
      // Heartbeat: update status topic only
      if (mqttClient.connected()) {
        publishStatus("online");
      }
    } else {
      // Unknown type: can be identified, but cannot be processed
      Serial.print("@NACK|");
      Serial.print(msgType);
      Serial.print("|");
      Serial.print(seq);
      Serial.println("|unknown_type");
      Serial.print("[SER] NACK: Unknown message type: ");
      Serial.println(msgType);
      return;
    }

    // ── Update Duplicate Protection State ────────────────────────────────
    strncpy(lastCompletedType, msgType, sizeof(lastCompletedType) - 1);
    lastCompletedType[sizeof(lastCompletedType) - 1] = '\0';
    strncpy(lastCompletedGwId, gwId, sizeof(lastCompletedGwId) - 1);
    lastCompletedGwId[sizeof(lastCompletedGwId) - 1] = '\0';
    lastCompletedSeq = seq;
    hasCompletedFrame = true;

    // ── Emit ACK Only After Synchronous Processing Path Has Finished ──────
    Serial.print("@ACK|");
    Serial.print(msgType);
    Serial.print("|");
    Serial.println(seq);
  }
}
