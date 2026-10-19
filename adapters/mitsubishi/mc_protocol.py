"""
adapters/mitsubishi/mc_protocol.py
──────────────────────────────────
Mitsubishi MELSEC-Q Series MC Protocol 3E Binary Adapter.
Strictly Read-Only implementation conforming to IPlcProtocolAdapter.

Supported Hardware:
  • Mitsubishi Electric MELSEC-Q Series with built-in Ethernet (specifically Q03UDECPU)
  • MELSEC-Q Ethernet modules (QJ71E71-100) running MC Protocol 3E binary

Limitations:
  • Does NOT support FX3G (requires 1E frame / dedicated serial protocol and octal addressing).
  • Does NOT support iQ-R series SLMP label-based access or 4E frames without configuration.
  • Contains ZERO write, force, or PLC control opcodes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import pymcprotocol

from core.adapter_interface import (
    AdapterCapabilities,
    IPlcProtocolAdapter,
    ProtocolMetadata,
)
from core.models import (
    DataQuality,
    NormalisedReading,
    NormalisedTelemetryBatch,
    PointDataType,
    PointDefinition,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Device address conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def _device_to_addr(device_str: str) -> Tuple[str, int]:
    """
    Parse a device string like 'X3' or 'Y20' into (device_type, decimal_address).

    Mitsubishi Q-series X/Y/B/W devices use hexadecimal numbering (base 16) in GX Works2
    and MC Protocol (unlike legacy FX series which used octal base 8).
    For example: X0-X7 are 0-7, Y20 is 0x20 = 32 decimal, Y27 is 0x27 = 39 decimal.
    Decimal devices: M, D, R, etc. use base 10.
    """
    if not device_str or len(device_str) < 2:
        raise ValueError(f"Invalid device address string: '{device_str}'")

    dev_type = device_str[0].upper()
    dev_num_str = device_str[1:]
    base = 16 if dev_type in ("X", "Y", "B", "W") else 10
    try:
        addr = int(dev_num_str, base)
    except ValueError:
        raise ValueError(f"Cannot parse device address from '{device_str}' (base {base})")
    return dev_type, addr


def _addr_to_label(dev_type: str, addr_decimal: int) -> str:
    """Return canonical device label (e.g. 'X3', 'Y20', 'D100') using proper base."""
    dev_type = dev_type.upper()
    if dev_type in ("X", "Y", "B", "W"):
        return f"{dev_type}{hex(addr_decimal)[2:].upper()}"
    return f"{dev_type}{addr_decimal}"


# ─────────────────────────────────────────────────────────────────────────────
# Mitsubishi MC Protocol Adapter Implementation
# ─────────────────────────────────────────────────────────────────────────────

class MitsubishiMcProtocolAdapter(IPlcProtocolAdapter):
    """
    STRICTLY READ-ONLY adapter for Mitsubishi Q-series via MC Protocol 3E binary.
    """

    def __init__(
        self,
        host: str = "192.168.3.39",
        port: int = 5010,
        timeout: float = 3.0,
        frame_type: str = "3E",
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.frame_type = frame_type

        self._client: Optional[pymcprotocol.Type3E] = None
        self._connected: bool = False
        self._packets_read: int = 0
        self._last_error: Optional[str] = None
        self._last_latency_ms: float = 0.0

        self._metadata = ProtocolMetadata(
            adapter_id="mitsubishi_mc_3e",
            display_name="Mitsubishi MC Protocol 3E (Binary)",
            version="1.0.0",
            manufacturer="Mitsubishi Electric",
            supported_models=[
                "MELSEC-Q (Q03UDECPU)",
                "MELSEC-Q (Built-in 100BASE-TX Ethernet)",
                "MELSEC-Q (QJ71E71-100)",
            ],
            capabilities=AdapterCapabilities(
                can_read_bits=True,
                can_read_words=True,
                can_read_floats=False,
                can_read_strings=False,
                supports_batch_optimization=True,
                supports_diagnostics=True,
                supports_device_identification=False,
                is_strictly_read_only=True,
            ),
        )

    @property
    def metadata(self) -> ProtocolMetadata:
        return self._metadata

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -------------------------------------------------------------------------
    # Lifecycle Methods
    # -------------------------------------------------------------------------

    def connect(self) -> bool:
        """Establish persistent TCP connection using pymcprotocol."""
        try:
            self._client = pymcprotocol.Type3E()
            timer_int = max(1, int(round(self.timeout)))
            self._client.setaccessopt(commtype="binary", timer_sec=timer_int)
            self._client.connect(self.host, self.port)
            self._connected = True
            self._last_error = None
            logger.info(
                "Mitsubishi MC Protocol connected to %s:%d (timeout=%ds)",
                self.host, self.port, timer_int
            )
            return True
        except Exception as exc:
            self._connected = False
            self._client = None
            self._last_error = str(exc)
            logger.error("Mitsubishi MC Protocol connection to %s:%d failed: %s", self.host, self.port, exc)
            return False

    def disconnect(self) -> None:
        """Close TCP socket safely."""
        try:
            if self._client and getattr(self._client, "_is_connected", False):
                self._client.close()
        except Exception as exc:
            logger.warning("Error during MC Protocol disconnect: %s", exc)
        finally:
            self._connected = False
            self._client = None
            logger.info("Mitsubishi MC Protocol disconnected")

    def test_connection(self) -> Dict[str, Any]:
        """Verify socket reachability."""
        start_t = time.monotonic()
        was_connected = self._connected
        connected = was_connected

        if not connected:
            connected = self.connect()

        latency_ms = (time.monotonic() - start_t) * 1000.0

        if not was_connected and connected:
            self.disconnect()

        return {
            "connected": connected,
            "latency_ms": round(latency_ms, 2),
            "error": self._last_error if not connected else None,
            "details": {
                "host": self.host,
                "port": self.port,
                "protocol": "MC Protocol 3E Binary",
            },
        }

    # -------------------------------------------------------------------------
    # Telemetry Polling (Strictly Read-Only)
    # -------------------------------------------------------------------------

    def read_points(
        self,
        points: List[PointDefinition],
        gateway_id: str = "plc-gw-01",
        device_id: str = "melsec_q03",
        sequence_number: int = 0,
    ) -> NormalisedTelemetryBatch:
        """
        Poll allowlisted points using MC Protocol batch-read operations.
        NEVER issues write opcodes.
        """
        start_t = time.monotonic()
        ts = utc_now_iso()

        if not self._connected or self._client is None:
            err = self._last_error or "Not connected to PLC"
            readings = {
                pt.point_id: NormalisedReading.create_failure(
                    point_id=pt.point_id,
                    error_msg=err,
                    quality=DataQuality.COMM_FAILURE,
                    timestamp_utc=ts,
                )
                for pt in points
            }
            return NormalisedTelemetryBatch(
                gateway_id=gateway_id,
                device_id=device_id,
                sequence_number=sequence_number,
                timestamp_utc=ts,
                readings=readings,
                connected=False,
                scan_duration_ms=0.0,
                error=err,
            )

        try:
            # Group points by device type for contiguous batch reading
            bit_groups: Dict[str, List[Tuple[PointDefinition, int]]] = {}
            word_groups: Dict[str, List[Tuple[PointDefinition, int]]] = {}

            for pt in points:
                dev_type, addr = _device_to_addr(pt.device_address)
                # Bit devices: X, Y, M, B
                if dev_type in ("X", "Y", "M", "B") or pt.data_type == PointDataType.BOOLEAN:
                    bit_groups.setdefault(dev_type, []).append((pt, addr))
                else:
                    word_groups.setdefault(dev_type, []).append((pt, addr))

            readings: Dict[str, NormalisedReading] = {}

            # Execute batch read for bit devices (MC opcode 0x0401, sub-command 0x0001)
            for dev_type, pt_addr_pairs in bit_groups.items():
                pt_addr_pairs.sort(key=lambda x: x[1])
                start_addr = pt_addr_pairs[0][1]
                end_addr = pt_addr_pairs[-1][1]
                read_size = end_addr - start_addr + 1

                head_label = _addr_to_label(dev_type, start_addr)
                raw_bits: List[int] = self._client.batchread_bitunits(
                    headdevice=head_label, readsize=read_size
                )

                # Map results back to PointDefinitions
                addr_to_val = {
                    start_addr + offset: int(bool(val))
                    for offset, val in enumerate(raw_bits)
                }

                for pt, addr in pt_addr_pairs:
                    if addr in addr_to_val:
                        bit_val = addr_to_val[addr]
                        readings[pt.point_id] = NormalisedReading.create_good(
                            point=pt,
                            raw_val=bit_val,
                            timestamp_utc=ts,
                        )
                    else:
                        readings[pt.point_id] = NormalisedReading.create_failure(
                            point_id=pt.point_id,
                            error_msg=f"Address {pt.device_address} missing from response",
                            quality=DataQuality.BAD,
                            timestamp_utc=ts,
                        )

            # Execute batch read for word devices (e.g. D registers) if any
            for dev_type, pt_addr_pairs in word_groups.items():
                pt_addr_pairs.sort(key=lambda x: x[1])
                start_addr = pt_addr_pairs[0][1]
                end_addr = pt_addr_pairs[-1][1]
                read_size = end_addr - start_addr + 1

                head_label = _addr_to_label(dev_type, start_addr)
                raw_words: List[int] = self._client.batchread_wordunits(
                    headdevice=head_label, readsize=read_size
                )

                addr_to_val = {
                    start_addr + offset: val
                    for offset, val in enumerate(raw_words)
                }

                for pt, addr in pt_addr_pairs:
                    if addr in addr_to_val:
                        word_val = addr_to_val[addr]
                        readings[pt.point_id] = NormalisedReading.create_good(
                            point=pt,
                            raw_val=word_val,
                            timestamp_utc=ts,
                        )
                    else:
                        readings[pt.point_id] = NormalisedReading.create_failure(
                            point_id=pt.point_id,
                            error_msg=f"Address {pt.device_address} missing from word response",
                            quality=DataQuality.BAD,
                            timestamp_utc=ts,
                        )

            self._packets_read += 1
            duration_ms = (time.monotonic() - start_t) * 1000.0
            self._last_latency_ms = duration_ms

            return NormalisedTelemetryBatch(
                gateway_id=gateway_id,
                device_id=device_id,
                sequence_number=sequence_number,
                timestamp_utc=ts,
                readings=readings,
                connected=True,
                scan_duration_ms=duration_ms,
                error=None,
            )

        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            logger.error("MC Protocol batchread failed: %s", exc)

            readings = {
                pt.point_id: NormalisedReading.create_failure(
                    point_id=pt.point_id,
                    error_msg=str(exc),
                    quality=DataQuality.COMM_FAILURE,
                    timestamp_utc=ts,
                )
                for pt in points
            }

            return NormalisedTelemetryBatch(
                gateway_id=gateway_id,
                device_id=device_id,
                sequence_number=sequence_number,
                timestamp_utc=ts,
                readings=readings,
                connected=False,
                scan_duration_ms=(time.monotonic() - start_t) * 1000.0,
                error=str(exc),
            )

    # -------------------------------------------------------------------------
    # Diagnostics & Metadata
    # -------------------------------------------------------------------------

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "adapter_id": self._metadata.adapter_id,
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "packets_read": self._packets_read,
            "last_latency_ms": round(self._last_latency_ms, 2),
            "last_error": self._last_error,
        }

    def get_device_info(self) -> Dict[str, str]:
        return {
            "vendor": "Mitsubishi Electric",
            "series": "MELSEC-Q",
            "frame": "3E Binary",
            "model_tested": "Q03UDECPU",
        }
