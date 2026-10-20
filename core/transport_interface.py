"""
core/transport_interface.py
───────────────────────────
Decoupled Telemetry Transport Interface and Implementations.
Ensures protocol adapters remain 100% agnostic of telemetry delivery mechanisms
(USB serial, MQTT, WebSockets, or HTTP).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from core.models import NormalisedTelemetryBatch, PointDefinition

logger = logging.getLogger(__name__)


class ITelemetryTransport(ABC):
    """
    Abstract interface for publishing telemetry and heartbeats to downstream consumers.
    """

    @abstractmethod
    def open(self) -> bool:
        """Initialize and open the transport channel. Returns True on success."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Gracefully close the transport channel."""
        pass

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True if the transport is open and ready to transmit."""
        pass

    @abstractmethod
    def publish_batch(
        self,
        batch: NormalisedTelemetryBatch,
        point_definitions: Optional[Dict[str, PointDefinition]] = None,
    ) -> bool:
        """
        Transmit a normalized telemetry batch.
        Returns True on successful transmission, False on failure.
        """
        pass

    @abstractmethod
    def publish_heartbeat(
        self,
        connected: bool,
        error: Optional[str] = None,
        gateway_id: str = "plc-gw-01",
    ) -> bool:
        """Send a periodic heartbeat / alive signal."""
        pass


class SerialTelemetryTransport(ITelemetryTransport):
    """
    Serial USB transport implementation for NodeMCU ESP8266 bridge.
    Implements stop-and-wait flow control with acknowledgements, bounded retries,
    duplicate protection, and thread-safe serial transaction serialization.
    Retains 100% backward compatibility with existing telemetry payload schema and framing.
    """

    def __init__(
        self,
        port: str = "COM9",
        baud_rate: int = 115200,
        timeout: float = 1.0,
        gateway_id: str = "plc-gw-01",
        ack_timeout: float = 5.0,
        max_retries: int = 1,
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.gateway_id = gateway_id
        self.ack_timeout = ack_timeout
        self.max_retries = max_retries
        self._seq: int = 0
        self._ser: Any = None
        self._lock = threading.Lock()
        self._rx_buffer = bytearray()

    def open(self) -> bool:
        with self._lock:
            try:
                import serial
                self._ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=self.timeout,
                )
                self._rx_buffer.clear()
                logger.info("Serial transport opened: %s @ %d baud (ack_timeout=%.1fs, max_retries=%d)",
                            self.port, self.baud_rate, self.ack_timeout, self.max_retries)
                return True
            except Exception as exc:
                logger.warning("Serial transport cannot open port %s: %s (will retry)", self.port, exc)
                self._ser = None
                return False

    def _close_locked(self) -> None:
        """Close and reset serial state while the caller already holds _lock."""
        if self._ser and getattr(self._ser, "is_open", False):
            try:
                self._ser.close()
            except Exception as exc:
                logger.warning("Error closing serial transport: %s", exc)
        self._ser = None
        self._rx_buffer.clear()
        logger.info("Serial transport closed")

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    @property
    def is_open(self) -> bool:
        return bool(self._ser and getattr(self._ser, "is_open", False))

    def format_line(self, payload: Dict[str, Any]) -> str:
        """Format payload dict into a newline-delimited JSON string."""
        return json.dumps(payload, separators=(",", ":")) + "\n"

    def publish_batch(
        self,
        batch: NormalisedTelemetryBatch,
        point_definitions: Optional[Dict[str, PointDefinition]] = None,
    ) -> bool:
        with self._lock:
            self._seq += 1
            # Convert to legacy JSON payload for ESP8266 firmware
            legacy = batch.to_legacy_payload(point_definitions)
            legacy["seq"] = self._seq
            legacy["gw_id"] = self.gateway_id
            return self._write_and_wait_ack_locked(legacy)

    def publish_heartbeat(
        self,
        connected: bool,
        error: Optional[str] = None,
        gateway_id: Optional[str] = None,
    ) -> bool:
        from core.models import utc_now_iso
        with self._lock:
            self._seq += 1
            payload = {
                "type": "heartbeat",
                "gw_id": gateway_id or self.gateway_id,
                "seq": self._seq,
                "ts": utc_now_iso(),
                "connected": connected,
                "error": error,
            }
            return self._write_and_wait_ack_locked(payload)

    def publish_error(self, error_msg: str, code: Optional[str] = None) -> bool:
        from core.models import utc_now_iso
        with self._lock:
            self._seq += 1
            payload = {
                "type": "gateway_error",
                "gw_id": self.gateway_id,
                "seq": self._seq,
                "ts": utc_now_iso(),
                "error": error_msg,
                "code": code,
            }
            return self._write_and_wait_ack_locked(payload)

    def _write(self, payload: Dict[str, Any]) -> bool:
        """Generic payload transmitter (thread-safe stop-and-wait)."""
        with self._lock:
            return self._write_and_wait_ack_locked(payload)

    def _read_next_line_locked(self, deadline: float) -> Optional[str]:
        """
        Extract the next complete newline-delimited line from the receive stream.
        Maintains internal RX buffer so pending bytes are never discarded.
        """
        while True:
            newline_idx = -1
            for i, b in enumerate(self._rx_buffer):
                if b in (10, 13):  # \n or \r
                    newline_idx = i
                    break

            if newline_idx >= 0:
                raw_line = bytes(self._rx_buffer[:newline_idx])
                del self._rx_buffer[:newline_idx + 1]
                while self._rx_buffer and self._rx_buffer[0] in (10, 13):
                    del self._rx_buffer[0]
                line_str = raw_line.decode("utf-8", errors="replace").strip()
                if line_str:
                    return line_str
                continue

            if not self.is_open:
                return None

            try:
                waiting = getattr(self._ser, "in_waiting", 0)
                if waiting > 0:
                    chunk = self._ser.read(min(waiting, 1024))
                    if chunk:
                        self._rx_buffer.extend(chunk)
                        continue
            except Exception as exc:
                logger.error("Error reading from serial port %s: %s", self.port, exc)
                self._close_locked()
                return None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.01, remaining))

    def _write_and_wait_ack_locked(self, payload: Dict[str, Any]) -> bool:
        """
        Write one complete frame and wait for matching @ACK|<type>|<seq>.
        Must be called while holding self._lock.
        """
        if not self.is_open:
            logger.debug("Serial transport not open – message seq=%s held/dropped", payload.get("seq"))
            return False

        target_type = str(payload.get("type", ""))
        target_seq = int(payload.get("seq", 0))
        target_gw_id = str(payload.get("gw_id", self.gateway_id))

        line = self.format_line(payload)
        frame_bytes = line.encode("utf-8")

        for attempt in range(self.max_retries + 1):
            try:
                self._ser.write(frame_bytes)
                if hasattr(self._ser, "flush"):
                    self._ser.flush()
            except Exception as exc:
                logger.error("Serial write failed on %s (attempt %d): %s", self.port, attempt + 1, exc)
                self._close_locked()
                return False

            deadline = time.monotonic() + self.ack_timeout
            protocol_error: Optional[str] = None
            while time.monotonic() < deadline:
                resp = self._read_next_line_locked(deadline)
                if resp is None:
                    continue

                if resp.startswith("@ACK|"):
                    parts = resp.split("|")
                    if len(parts) >= 3:
                        ack_type = parts[1]
                        ack_seq_str = parts[2]
                        if len(parts) >= 4:
                            ack_gw_id = parts[1]
                            ack_type = parts[2]
                            ack_seq_str = parts[3]
                            if ack_gw_id != target_gw_id:
                                logger.debug("Ignored ACK with mismatched gw_id: %s", resp)
                                continue

                        try:
                            ack_seq = int(ack_seq_str)
                        except ValueError:
                            logger.debug("Ignored ACK with malformed sequence: %s", resp)
                            continue

                        if ack_type == target_type and ack_seq == target_seq:
                            logger.debug("ACK verified for %s seq=%d (attempt %d)", target_type, target_seq, attempt + 1)
                            return True
                        else:
                            logger.debug("Ignored ACK for non-matching frame (%s seq=%d, expected %s seq=%d)",
                                         ack_type, ack_seq, target_type, target_seq)
                            continue

                elif resp.startswith("@NACK|"):
                    parts = resp.split("|")
                    if len(parts) >= 3:
                        nack_type = parts[1]
                        nack_seq_str = parts[2]
                        reason = parts[3] if len(parts) > 3 else "unspecified"
                        try:
                            nack_seq = int(nack_seq_str)
                        except ValueError:
                            nack_seq = -1

                        if nack_type == target_type and nack_seq == target_seq:
                            logger.warning("NACK received from ESP8266 for %s seq=%d: %s",
                                           target_type, target_seq, reason)
                            return False
                        else:
                            logger.debug("Ignored NACK for different frame: %s", resp)
                            continue

                elif resp.startswith("@PROTO_ERR|"):
                    parts = resp.split("|", 1)
                    reason = parts[1] if len(parts) > 1 else "unspecified"
                    logger.warning(
                        "Protocol error from ESP8266 during transaction for %s seq=%d (attempt %d/%d): %s",
                        target_type, target_seq, attempt + 1, self.max_retries + 1, resp
                    )
                    protocol_error = reason
                    break

                else:
                    logger.info("[ESP8266] %s", resp)

            if protocol_error is not None:
                if attempt < self.max_retries:
                    logger.warning(
                        "Protocol error (%s) on attempt %d – retrying identical frame (%s seq=%d)",
                        protocol_error, attempt + 1, target_type, target_seq
                    )
                    continue
                else:
                    logger.error(
                        "Transaction failed: protocol error (%s) for %s seq=%d after %d attempt(s)",
                        protocol_error, target_type, target_seq, attempt + 1
                    )
                    return False

            if attempt < self.max_retries:
                logger.warning(
                    "Timeout waiting for ACK (%s seq=%d) after %.1fs – retrying frame (1/%d)",
                    target_type, target_seq, self.ack_timeout, self.max_retries
                )
            else:
                logger.error(
                    "Transaction failed: no matching ACK for %s seq=%d after %d attempt(s)",
                    target_type, target_seq, attempt + 1
                )
                return False

        return False


class MockTelemetryTransport(ITelemetryTransport):
    """
    In-memory mock transport for deterministic testing without serial hardware.
    Captures transmitted frames for assertion verification.
    """

    def __init__(self, gateway_id: str = "test-gw") -> None:
        self.gateway_id = gateway_id
        self.is_connected = False
        self.sent_batches: list[NormalisedTelemetryBatch] = []
        self.sent_raw_payloads: list[Dict[str, Any]] = []
        self.heartbeats: list[Dict[str, Any]] = []

    def open(self) -> bool:
        self.is_connected = True
        return True

    def close(self) -> None:
        self.is_connected = False

    @property
    def is_open(self) -> bool:
        return self.is_connected

    def publish_batch(
        self,
        batch: NormalisedTelemetryBatch,
        point_definitions: Optional[Dict[str, PointDefinition]] = None,
    ) -> bool:
        if not self.is_open:
            return False
        self.sent_batches.append(batch)
        payload = batch.to_legacy_payload(point_definitions)
        self.sent_raw_payloads.append(payload)
        return True

    def publish_heartbeat(
        self,
        connected: bool,
        error: Optional[str] = None,
        gateway_id: Optional[str] = None,
    ) -> bool:
        if not self.is_open:
            return False
        self.heartbeats.append({
            "connected": connected,
            "error": error,
            "gateway_id": gateway_id or self.gateway_id,
        })
        return True
