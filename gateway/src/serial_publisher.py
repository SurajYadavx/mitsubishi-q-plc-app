"""
serial_publisher.py
───────────────────
Serialises a DeviceReading to newline-delimited JSON and writes it to the
USB serial port that the NodeMCU ESP8266 is listening on.

JSON schema (one line per message, terminated with \\n):
{
  "type"      : "plc_reading",          // or "heartbeat" | "gateway_error"
  "gw_id"     : "plc-gw-01",
  "seq"       : 12345,                  // monotonic counter
  "ts"        : "2025-01-01T12:00:00Z", // ISO-8601 UTC
  "connected" : true,
  "inputs"    : { "X0":0,"X1":0,"X2":0,"X3":1,"X4":0,"X5":0,"X6":0,"X7":0 },
  "outputs"   : { "Y20":1,"Y21":0,"Y22":0,"Y23":0,"Y24":0,"Y25":0,"Y26":0,"Y27":0 },
  "error"     : null                    // string if error, else null
}

Heartbeat (no PLC data change, just alive signal):
{
  "type"      : "heartbeat",
  "gw_id"     : "plc-gw-01",
  "seq"       : 12346,
  "ts"        : "2025-01-01T12:00:05Z",
  "connected" : true,
  "error"     : null
}

Gateway error (PLC unreachable):
{
  "type"      : "gateway_error",
  "gw_id"     : "plc-gw-01",
  "seq"       : 12347,
  "ts"        : "2025-01-01T12:00:10Z",
  "connected" : false,
  "error"     : "Connection refused"
}
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import serial  # pyserial

from plc_reader import DeviceReading

logger = logging.getLogger(__name__)


class SerialPublisher:
    """Write JSON-framed PLC readings to a USB serial port."""

    def __init__(
        self,
        port: str,
        baud_rate: int = 115200,
        timeout: float = 1.0,
        gateway_id: str = "plc-gw-01",
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.gateway_id = gateway_id
        self._seq: int = 0
        self._ser: Optional[serial.Serial] = None

    # ------------------------------------------------------------------
    def open(self) -> bool:
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
            )
            logger.info("Serial port opened: %s @ %d baud", self.port, self.baud_rate)
            return True
        except serial.SerialException as exc:
            logger.error("Cannot open serial port %s: %s", self.port, exc)
            return False

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        logger.info("Serial port closed")

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    # ------------------------------------------------------------------
    def build_reading_payload(self, reading: DeviceReading) -> dict:
        """Construct the dictionary payload for a DeviceReading."""
        msg_type = "plc_reading" if reading.connected else "gateway_error"
        payload: dict = {
            "type": msg_type,
            "gw_id": self.gateway_id,
            "seq": self._seq,
            "ts": reading.timestamp_iso,
            "connected": reading.connected,
            "error": reading.error,
        }
        if reading.connected:
            payload["inputs"] = reading.inputs
            payload["outputs"] = reading.outputs
        return payload

    def build_heartbeat_payload(self, connected: bool, error: Optional[str] = None) -> dict:
        """Construct the dictionary payload for a heartbeat."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
        return {
            "type": "heartbeat",
            "gw_id": self.gateway_id,
            "seq": self._seq,
            "ts": ts,
            "connected": connected,
            "error": error,
        }

    def format_payload(self, payload: dict) -> str:
        """Format a payload dict into a newline-delimited JSON string."""
        return json.dumps(payload, separators=(",", ":")) + "\n"

    # ------------------------------------------------------------------
    def publish_reading(self, reading: DeviceReading) -> bool:
        """Serialise a DeviceReading and write it to the serial port."""
        self._seq += 1
        payload = self.build_reading_payload(reading)
        return self._write(payload)

    # ------------------------------------------------------------------
    def publish_heartbeat(self, connected: bool, error: Optional[str] = None) -> bool:
        """Send an alive heartbeat when no data changed."""
        self._seq += 1
        payload = self.build_heartbeat_payload(connected, error)
        return self._write(payload)

    # ------------------------------------------------------------------
    def _write(self, payload: dict) -> bool:
        if not self.is_open:
            logger.warning("Serial port not open – dropping message seq=%d", payload.get("seq"))
            return False
        try:
            line = self.format_payload(payload)
            self._ser.write(line.encode("utf-8"))  # type: ignore[union-attr]
            self._ser.flush()                       # type: ignore[union-attr]
            logger.debug("Serial TX  seq=%d  type=%s", payload.get("seq"), payload.get("type"))
            return True
        except serial.SerialException as exc:
            logger.error("Serial write error: %s", exc)
            return False
