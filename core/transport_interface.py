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
    Retains 100% backward compatibility with the existing hardware pipeline and firmware schema.
    """

    def __init__(
        self,
        port: str = "COM9",
        baud_rate: int = 115200,
        timeout: float = 1.0,
        gateway_id: str = "plc-gw-01",
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.gateway_id = gateway_id
        self._seq: int = 0
        self._ser: Any = None

    def open(self) -> bool:
        try:
            import serial
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
            )
            logger.info("Serial transport opened: %s @ %d baud", self.port, self.baud_rate)
            return True
        except Exception as exc:
            logger.warning("Serial transport cannot open port %s: %s (will retry)", self.port, exc)
            self._ser = None
            return False

    def close(self) -> None:
        if self._ser and getattr(self._ser, "is_open", False):
            try:
                self._ser.close()
            except Exception as exc:
                logger.warning("Error closing serial transport: %s", exc)
        self._ser = None
        logger.info("Serial transport closed")

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
        self._seq += 1
        # Convert to legacy JSON payload for ESP8266 firmware
        legacy = batch.to_legacy_payload(point_definitions)
        legacy["seq"] = self._seq
        legacy["gw_id"] = self.gateway_id
        return self._write(legacy)

    def publish_heartbeat(
        self,
        connected: bool,
        error: Optional[str] = None,
        gateway_id: Optional[str] = None,
    ) -> bool:
        from core.models import utc_now_iso
        self._seq += 1
        payload = {
            "type": "heartbeat",
            "gw_id": gateway_id or self.gateway_id,
            "seq": self._seq,
            "ts": utc_now_iso(),
            "connected": connected,
            "error": error,
        }
        return self._write(payload)

    def _write(self, payload: Dict[str, Any]) -> bool:
        if not self.is_open:
            logger.debug("Serial transport not open – message seq=%d held/dropped", payload.get("seq"))
            return False
        try:
            line = self.format_line(payload)
            self._ser.write(line.encode("utf-8"))
            return True
        except Exception as exc:
            logger.error("Serial write failed on %s: %s", self.port, exc)
            self.close()
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
