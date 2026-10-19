"""
core/adapter_interface.py
─────────────────────────
Common Read-Only PLC Protocol Adapter Interface.
Enforces strictly read-only industrial telemetry acquisition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models import NormalisedTelemetryBatch, PointDefinition


class UnsupportedOperationError(Exception):
    """Raised when an unsupported or prohibited operation (such as a write command) is attempted."""
    pass


class ReadOnlySecurityViolation(UnsupportedOperationError):
    """
    Raised when an attempt is made to invoke write, force, reset, or modify operations
    on a read-only industrial monitoring adapter.
    """
    pass


@dataclass(frozen=True)
class AdapterCapabilities:
    """Explicit declaration of adapter functional boundaries."""
    can_read_bits: bool = True
    can_read_words: bool = True
    can_read_floats: bool = False
    can_read_strings: bool = False
    supports_batch_optimization: bool = True
    supports_diagnostics: bool = True
    supports_device_identification: bool = False
    is_strictly_read_only: bool = True


@dataclass(frozen=True)
class ProtocolMetadata:
    """Protocol adapter identification and compatibility metadata."""
    adapter_id: str                      # e.g. "mitsubishi_mc_3e", "modbus_tcp", "simulated"
    display_name: str                    # e.g. "Mitsubishi MC Protocol Type 3E (Binary)"
    version: str                         # e.g. "1.0.0"
    manufacturer: str                    # e.g. "Mitsubishi Electric"
    supported_models: List[str] = field(default_factory=list)
    capabilities: AdapterCapabilities = field(default_factory=AdapterCapabilities)


class IPlcProtocolAdapter(ABC):
    """
    Abstract Base Class for all industrial PLC protocol adapters.
    
    SAFETY INVARIANT:
    All implementations are STRICTLY READ-ONLY.
    No subclass shall expose or execute write commands, force coils,
    download ladder logic, or transition controller operating modes.
    """

    @property
    @abstractmethod
    def metadata(self) -> ProtocolMetadata:
        """Return adapter identification, version, and capability declarations."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if connection to target controller is active."""
        pass

    @abstractmethod
    def connect(self) -> bool:
        """
        Open communication channel (TCP socket, serial port, or bus handle).
        Returns True on successful connection, False on failure.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully close communication channel and release system resources."""
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Perform a safe, non-destructive connection verification.
        Returns a dictionary containing diagnostics:
          {"connected": bool, "latency_ms": float, "error": Optional[str], "details": dict}
        """
        pass

    @abstractmethod
    def read_points(
        self,
        points: List[PointDefinition],
        gateway_id: str = "plc-gw-01",
        device_id: str = "default_device",
        sequence_number: int = 0,
    ) -> NormalisedTelemetryBatch:
        """
        Read the requested allowlisted points from the field device.
        Must return a NormalisedTelemetryBatch with explicit DataQuality indicators.
        Communication errors MUST be logged and returned as COMM_FAILURE readings,
        never as fabricated zeros.
        """
        pass

    @abstractmethod
    def get_diagnostics(self) -> Dict[str, Any]:
        """Query driver runtime diagnostics (packet counters, timeouts, last error)."""
        pass

    def get_device_info(self) -> Dict[str, str]:
        """
        Query read-only device identification (vendor, CPU model, firmware, serial).
        Returns empty dict if the underlying protocol does not support identification.
        """
        return {}

    # ── Read-Only Security Invariant Enforcement ──────────────────────────────
    def write(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Write operations are strictly prohibited on read-only adapter '{self.metadata.adapter_id}'."
        )

    def force_coil(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Forcing outputs is strictly prohibited on read-only adapter '{self.metadata.adapter_id}'."
        )

    def write_register(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Writing registers is strictly prohibited on read-only adapter '{self.metadata.adapter_id}'."
        )
