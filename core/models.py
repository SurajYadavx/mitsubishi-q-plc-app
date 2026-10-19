"""
core/models.py
──────────────
Normalised industrial telemetry data models for the Universal PLC Platform.
Guarantees strict separation between raw machine registers, engineering units,
data quality metrics, and legacy payload serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Union


class DataQuality(str, Enum):
    """
    Industrial data quality classification (conforming to OPC DA / IEC 61158).
    Ensures downstream consumers can distinguish valid 0/False values from faults.
    """
    GOOD = "GOOD"                    # Valid live sample directly from controller
    BAD = "BAD"                      # Sensor out of range or driver failure
    UNCERTAIN = "UNCERTAIN"          # Degradation detected or manual test value
    STALE = "STALE"                  # Value has not refreshed within expected timeout
    COMM_FAILURE = "COMM_FAILURE"    # Communication socket/serial lost; reading is invalid


class PointDataType(str, Enum):
    """Standardized industrial data types across protocol adapters."""
    BOOLEAN = "BOOLEAN"
    INT16 = "INT16"
    UINT16 = "UINT16"
    INT32 = "INT32"
    UINT32 = "UINT32"
    FLOAT32 = "FLOAT32"
    STRING = "STRING"


def utc_now_iso() -> str:
    """Generate a timezone-aware ISO-8601 UTC timestamp string."""
    now = datetime.now(timezone.utc)
    # Format with explicit Z UTC indicator
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"


@dataclass(frozen=True)
class PointDefinition:
    """
    Immutable specification of a monitored point / tag.
    
    Attributes:
        point_id: Unique identifier (e.g. "pushbutton_x3", "main_pump.speed")
        device_address: Protocol-specific native address (e.g. "X3", "Y20", "40001", "D100")
        data_type: Expected data type (BOOLEAN, INT16, etc.)
        engineering_units: Unit descriptor (e.g. "RPM", "bar", "degC")
        scale_factor: Multiplier for raw reading: engineering = (raw * scale) + offset
        offset: Additive constant for scaling
        description: Human-readable point documentation
    """
    point_id: str
    device_address: str
    data_type: PointDataType = PointDataType.BOOLEAN
    engineering_units: str = ""
    scale_factor: float = 1.0
    offset: float = 0.0
    description: str = ""

    def __post_init__(self) -> None:
        if not self.point_id or not isinstance(self.point_id, str):
            raise ValueError("point_id must be a non-empty string")
        if not self.device_address or not isinstance(self.device_address, str):
            raise ValueError(f"device_address must be a non-empty string for point '{self.point_id}'")
        if not isinstance(self.data_type, PointDataType):
            if isinstance(self.data_type, str):
                try:
                    object.__setattr__(self, "data_type", PointDataType(self.data_type.upper()))
                except ValueError:
                    raise ValueError(f"Unsupported data_type '{self.data_type}' for point '{self.point_id}'")
            else:
                raise ValueError(f"Invalid data_type type for point '{self.point_id}'")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "point_id": self.point_id,
            "device_address": self.device_address,
            "data_type": self.data_type.value,
            "engineering_units": self.engineering_units,
            "scale_factor": self.scale_factor,
            "offset": self.offset,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PointDefinition:
        return cls(
            point_id=data["point_id"],
            device_address=data["device_address"],
            data_type=PointDataType(data.get("data_type", "BOOLEAN")),
            engineering_units=data.get("engineering_units", ""),
            scale_factor=float(data.get("scale_factor", 1.0)),
            offset=float(data.get("offset", 0.0)),
            description=data.get("description", ""),
        )


@dataclass
class NormalisedReading:
    """
    Normalized data sample for an individual point.
    
    CRITICAL SAFETY INVARIANT:
    `raw_value=0` or `raw_value=False` is a VALID reading with DataQuality.GOOD.
    A failed reading MUST have `raw_value=None` and `quality=DataQuality.BAD` or `COMM_FAILURE`.
    """
    point_id: str
    raw_value: Any
    engineering_value: Any
    quality: DataQuality
    timestamp_utc: str = field(default_factory=utc_now_iso)
    source_timestamp: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Return True only if data quality is GOOD and raw_value is not None."""
        return self.quality == DataQuality.GOOD and self.raw_value is not None

    @classmethod
    def create_good(
        cls,
        point: PointDefinition,
        raw_val: Any,
        timestamp_utc: Optional[str] = None,
        source_ts: Optional[str] = None,
    ) -> NormalisedReading:
        """Construct a valid GOOD reading, applying scaling if configured."""
        ts = timestamp_utc or utc_now_iso()
        
        # Calculate engineering value
        eng_val = raw_val
        if raw_val is not None and isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            if point.scale_factor != 1.0 or point.offset != 0.0:
                eng_val = round((raw_val * point.scale_factor) + point.offset, 4)
        elif isinstance(raw_val, bool):
            eng_val = int(raw_val)

        return cls(
            point_id=point.point_id,
            raw_value=raw_val,
            engineering_value=eng_val,
            quality=DataQuality.GOOD,
            timestamp_utc=ts,
            source_timestamp=source_ts,
            error_message=None,
        )

    @classmethod
    def create_failure(
        cls,
        point_id: str,
        error_msg: str,
        quality: DataQuality = DataQuality.COMM_FAILURE,
        timestamp_utc: Optional[str] = None,
    ) -> NormalisedReading:
        """Construct an explicit failure reading with null values and diagnostic error."""
        return cls(
            point_id=point_id,
            raw_value=None,
            engineering_value=None,
            quality=quality,
            timestamp_utc=timestamp_utc or utc_now_iso(),
            error_message=error_msg,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "point_id": self.point_id,
            "raw_value": self.raw_value,
            "engineering_value": self.engineering_value,
            "quality": self.quality.value,
            "timestamp_utc": self.timestamp_utc,
            "source_timestamp": self.source_timestamp,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NormalisedReading:
        return cls(
            point_id=data["point_id"],
            raw_value=data.get("raw_value"),
            engineering_value=data.get("engineering_value"),
            quality=DataQuality(data.get("quality", "BAD")),
            timestamp_utc=data.get("timestamp_utc", utc_now_iso()),
            source_timestamp=data.get("source_timestamp"),
            error_message=data.get("error_message"),
        )


@dataclass
class NormalisedTelemetryBatch:
    """
    Atomic telemetry frame from one device poll cycle.
    Contains both the full normalized point registry and backward-compatible serialization.
    """
    gateway_id: str
    device_id: str
    sequence_number: int
    timestamp_utc: str = field(default_factory=utc_now_iso)
    readings: Dict[str, NormalisedReading] = field(default_factory=dict)
    connected: bool = False
    scan_duration_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize full normalized batch to a standard JSON-compatible dictionary."""
        return {
            "type": "universal_telemetry",
            "gw_id": self.gateway_id,
            "device_id": self.device_id,
            "seq": self.sequence_number,
            "ts": self.timestamp_utc,
            "connected": self.connected,
            "error": self.error,
            "scan_duration_ms": round(self.scan_duration_ms, 2),
            "readings": {pid: r.to_dict() for pid, r in self.readings.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NormalisedTelemetryBatch:
        readings = {}
        for pid, r_dict in data.get("readings", {}).items():
            readings[pid] = NormalisedReading.from_dict(r_dict)
        return cls(
            gateway_id=data.get("gw_id", ""),
            device_id=data.get("device_id", ""),
            sequence_number=int(data.get("seq", 0)),
            timestamp_utc=data.get("ts", utc_now_iso()),
            readings=readings,
            connected=bool(data.get("connected", False)),
            scan_duration_ms=float(data.get("scan_duration_ms", 0.0)),
            error=data.get("error"),
        )

    def to_legacy_payload(
        self,
        point_definitions: Optional[Dict[str, PointDefinition]] = None,
    ) -> Dict[str, Any]:
        """
        Export to the legacy JSON schema required by:
          1. SerialPublisher (COM9 to NodeMCU)
          2. ESP8266 Firmware (plc_monitor.ino)
          3. Supervisory Dashboard (app.js)
        
        Legacy schema:
        {
          "type": "plc_reading" | "gateway_error",
          "gw_id": "plc-gw-01",
          "seq": 12345,
          "ts": "2026-10-15T12:00:00Z",
          "connected": true,
          "inputs":  { "X0":0, "X1":0, ..., "X7":0 },
          "outputs": { "Y20":1, ..., "Y27":0 },
          "error": null
        }
        """
        msg_type = "plc_reading" if self.connected else "gateway_error"
        payload: Dict[str, Any] = {
            "type": msg_type,
            "gw_id": self.gateway_id,
            "seq": self.sequence_number,
            "ts": self.timestamp_utc,
            "connected": self.connected,
            "error": self.error,
        }

        if self.connected:
            inputs: Dict[str, int] = {}
            outputs: Dict[str, int] = {}

            for point_id, reading in self.readings.items():
                # Determine legacy device address (e.g. X3, Y20)
                addr = point_id
                if point_definitions and point_id in point_definitions:
                    addr = point_definitions[point_id].device_address

                addr_upper = addr.upper()
                val = reading.raw_value
                bit_val = int(bool(val)) if val is not None else 0

                if addr_upper.startswith("X"):
                    inputs[addr_upper] = bit_val
                elif addr_upper.startswith("Y"):
                    outputs[addr_upper] = bit_val
                else:
                    # Generic input fallback
                    inputs[addr_upper] = bit_val

            payload["inputs"] = inputs
            payload["outputs"] = outputs

        return payload
