"""
adapters/modbus/modbus_tcp.py
─────────────────────────────
Universal Modbus TCP Read-Only Protocol Adapter.
Implements IPlcProtocolAdapter for monitoring Modbus TCP controllers (e.g. Horner OCS,
Schneider, WAGO, Beckhoff, and generic RTUs).

STRICTLY READ-ONLY:
Supports only read operations:
  • FC 0x01: Read Coils
  • FC 0x02: Read Discrete Inputs
  • FC 0x03: Read Holding Registers
  • FC 0x04: Read Input Registers

Prohibits all write function codes (FC 0x05, 0x06, 0x0F, 0x10).
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException

from core.adapter_interface import (
    AdapterCapabilities,
    IPlcProtocolAdapter,
    ProtocolMetadata,
    ReadOnlySecurityViolation,
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
# Addressing Models and Parser
# ─────────────────────────────────────────────────────────────────────────────

class ModbusTable(str, Enum):
    """The four standard Modbus memory tables."""
    COIL = "COIL"                           # FC 0x01 (1-bit discrete output/internal)
    DISCRETE_INPUT = "DISCRETE_INPUT"       # FC 0x02 (1-bit discrete physical input)
    HOLDING_REGISTER = "HOLDING_REGISTER"   # FC 0x03 (16-bit analog/output register)
    INPUT_REGISTER = "INPUT_REGISTER"       # FC 0x04 (16-bit analog/input register)


@dataclass(frozen=True)
class ModbusAddress:
    """
    Parsed Modbus memory address.
    
    Attributes:
        table: Memory table (COIL, DISCRETE_INPUT, HOLDING_REGISTER, INPUT_REGISTER)
        offset: 0-based protocol wire address (0 to 65535)
        raw_string: Original unparsed address string
        bit_index: Optional bit index within a 16-bit register (0-15)
    """
    table: ModbusTable
    offset: int
    raw_string: str
    bit_index: Optional[int] = None


def parse_modbus_address(address_str: str) -> ModbusAddress:
    """
    Parse a Modbus address string into a canonical ModbusAddress structure.

    Supported Address Conventions:
      1. Explicit Prefix (0-based offset):
         • 'c:0', 'coil:10'        -> Coils, offset 0, 10
         • 'di:0', 'disc:5'        -> Discrete Inputs, offset 0, 5
         • 'hr:0', 'holding:100'   -> Holding Registers, offset 0, 100
         • 'ir:0', 'input:20'      -> Input Registers, offset 0, 20
         • 'hr:0.3'                -> Bit 3 of Holding Register 0
      
      2. Standard Modicon 5-Digit / 6-Digit Notation (1-based address):
         • 00001 - 09999 (000001 - 065536) -> Coils (offset = addr - 1)
         • 10001 - 19999 (100001 - 165536) -> Discrete Inputs (offset = addr - 10001 or - 100001)
         • 30001 - 39999 (300001 - 365536) -> Input Registers (offset = addr - 30001 or - 300001)
         • 40001 - 49999 (400001 - 465536) -> Holding Registers (offset = addr - 40001 or - 400001)

      3. Pure Integer Fallback (defaults to Holding Register offset).
    """
    if not address_str or not isinstance(address_str, str):
        raise ValueError("Modbus address must be a non-empty string")

    raw = address_str.strip().lower()

    # Check for bit-within-register notation (e.g. 'hr:0.3' or '40001.3')
    bit_index: Optional[int] = None
    if "." in raw:
        base_part, bit_part = raw.split(".", 1)
        try:
            bit_index = int(bit_part)
            if not 0 <= bit_index <= 15:
                raise ValueError
        except ValueError:
            raise ValueError(f"Invalid bit index '{bit_part}' in Modbus address '{address_str}' (must be 0-15)")
        raw = base_part

    # 1. Prefix Form
    prefixes = [
        (("coil:", "c:"), ModbusTable.COIL),
        (("di:", "disc:", "discrete:"), ModbusTable.DISCRETE_INPUT),
        (("hr:", "holding:"), ModbusTable.HOLDING_REGISTER),
        (("ir:", "input:"), ModbusTable.INPUT_REGISTER),
    ]

    for prefix_tuple, table in prefixes:
        for pfx in prefix_tuple:
            if raw.startswith(pfx):
                num_str = raw[len(pfx):]
                try:
                    offset = int(num_str)
                except ValueError:
                    raise ValueError(f"Invalid offset '{num_str}' in Modbus address '{address_str}'")
                if not 0 <= offset <= 65535:
                    raise ValueError(f"Modbus offset {offset} out of range (0-65535)")
                if table in (ModbusTable.COIL, ModbusTable.DISCRETE_INPUT) and bit_index is not None:
                    raise ValueError(f"Bit indexing is not applicable to 1-bit discrete point '{address_str}'")
                return ModbusAddress(table=table, offset=offset, raw_string=address_str, bit_index=bit_index)

    # 2. Modicon 5-digit / 6-digit Notation
    if raw.isdigit():
        val = int(raw)
        digits = len(raw)

        if digits in (5, 6):
            if raw.startswith("0"):
                # Coil: 00001 -> 0
                offset = val - 1
                table = ModbusTable.COIL
            elif raw.startswith("1"):
                # Discrete input: 10001 -> 0, 100001 -> 0
                base_val = 100001 if digits == 6 else 10001
                offset = val - base_val
                table = ModbusTable.DISCRETE_INPUT
            elif raw.startswith("3"):
                # Input register: 30001 -> 0, 300001 -> 0
                base_val = 300001 if digits == 6 else 30001
                offset = val - base_val
                table = ModbusTable.INPUT_REGISTER
            elif raw.startswith("4"):
                # Holding register: 40001 -> 0, 400001 -> 0
                base_val = 400001 if digits == 6 else 40001
                offset = val - base_val
                table = ModbusTable.HOLDING_REGISTER
            else:
                raise ValueError(
                    f"Invalid Modicon reference '{address_str}'. Standard Modicon references must begin with "
                    "0 (Coil), 1 (Discrete Input), 3 (Input Register), or 4 (Holding Register). "
                    f"For explicit register offsets, use prefix syntax (e.g. 'hr:{val}')."
                )

            if table in (ModbusTable.COIL, ModbusTable.DISCRETE_INPUT) and bit_index is not None:
                raise ValueError(f"Bit indexing is not applicable to 1-bit discrete point '{address_str}'")

            if 0 <= offset <= 65535:
                return ModbusAddress(table=table, offset=offset, raw_string=address_str, bit_index=bit_index)
            raise ValueError(f"Calculated Modicon offset {offset} out of range (0-65535) in '{address_str}'")

        # Shorter integer: treat as direct 0-based holding register offset
        if 0 <= val <= 65535:
            return ModbusAddress(table=ModbusTable.HOLDING_REGISTER, offset=val, raw_string=address_str, bit_index=bit_index)

    raise ValueError(
        f"Unrecognized Modbus address syntax '{address_str}'. "
        "Use explicit prefix (e.g. 'hr:0', 'coil:5', 'di:0', 'ir:10') or Modicon notation (e.g. '40001', '00001')."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Data Conversion Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_registers(
    registers: List[int],
    data_type: PointDataType,
    word_order: str = "big_endian",
) -> Any:
    """
    Decode 16-bit register words into normalized data types, respecting word order.
    
    Word order conventions:
      • 'big_endian' (ABCD): High word first (Modbus standard)
      • 'little_endian' / 'word_swap' (CDAB): Low word first
    """
    if not registers:
        raise ValueError(f"No register data provided to decode {data_type.value}")

    if data_type == PointDataType.BOOLEAN:
        return 1 if (registers[0] & 0xFFFF) != 0 else 0

    if data_type == PointDataType.UINT16:
        return registers[0] & 0xFFFF

    if data_type == PointDataType.INT16:
        raw_bytes = struct.pack(">H", registers[0] & 0xFFFF)
        return struct.unpack(">h", raw_bytes)[0]

    # 32-bit types require 2 registers
    if data_type in (PointDataType.UINT32, PointDataType.INT32, PointDataType.FLOAT32):
        if len(registers) < 2:
            raise ValueError(
                f"Incomplete read: decoding {data_type.value} requires 2 consecutive registers, got {len(registers)}"
            )

        w0, w1 = registers[0] & 0xFFFF, registers[1] & 0xFFFF

        # Apply word order
        order = word_order.lower().strip()
        if order in ("little_endian", "word_swap"):
            raw_bytes = struct.pack(">HH", w1, w0)
        elif order in ("big_endian", "standard"):
            raw_bytes = struct.pack(">HH", w0, w1)
        else:
            raise ValueError(f"Unsupported word order '{word_order}'. Expected 'big_endian' or 'word_swap'/'little_endian'.")

        if data_type == PointDataType.UINT32:
            return struct.unpack(">I", raw_bytes)[0]

        if data_type == PointDataType.INT32:
            return struct.unpack(">i", raw_bytes)[0]

        if data_type == PointDataType.FLOAT32:
            val = struct.unpack(">f", raw_bytes)[0]
            return round(val, 6)

    if data_type == PointDataType.STRING:
        # Decode registers as ASCII bytes
        b_list = bytearray()
        for r in registers:
            b_list.extend(struct.pack(">H", r & 0xFFFF))
        return b_list.decode("ascii", errors="replace").rstrip("\x00").strip()

    raise ValueError(f"Unsupported PointDataType '{data_type}' for register decoding")


# ─────────────────────────────────────────────────────────────────────────────
# Modbus TCP Protocol Adapter Implementation
# ─────────────────────────────────────────────────────────────────────────────

class ModbusTcpAdapter(IPlcProtocolAdapter):
    """
    STRICTLY READ-ONLY Modbus TCP protocol adapter.
    Conforms to IPlcProtocolAdapter.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 502,
        unit_id: int = 1,
        timeout: float = 3.0,
        word_order: str = "big_endian",
    ) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.word_order = word_order

        self._client: Optional[ModbusTcpClient] = None
        self._connected: bool = False
        self._packets_read: int = 0
        self._last_error: Optional[str] = None
        self._last_latency_ms: float = 0.0

        self._metadata = ProtocolMetadata(
            adapter_id="modbus_tcp",
            display_name="Modbus TCP (Read-Only)",
            version="1.0.0",
            manufacturer="Modbus Organization / Generic",
            supported_models=[
                "Horner OCS (Modbus Slave)",
                "Schneider Electric",
                "WAGO 750 Series",
                "Beckhoff CX",
                "Generic Modbus TCP Server",
            ],
            capabilities=AdapterCapabilities(
                can_read_bits=True,
                can_read_words=True,
                can_read_floats=True,
                can_read_strings=True,
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
        return bool(self._connected and self._client and self._client.connected)

    # -------------------------------------------------------------------------
    # Lifecycle Methods
    # -------------------------------------------------------------------------

    def connect(self) -> bool:
        """Establish TCP connection to Modbus server."""
        try:
            if self._client:
                self.disconnect()
            self._client = ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout,
            )
            ok = self._client.connect()
            self._connected = bool(ok)
            if ok:
                self._last_error = None
                logger.info("Modbus TCP connected to %s:%d (unit_id=%d)", self.host, self.port, self.unit_id)
            else:
                self._last_error = "TCP connect returned False"
                logger.warning("Modbus TCP connection failed to %s:%d", self.host, self.port)
            return self._connected
        except Exception as exc:
            self._connected = False
            self._client = None
            self._last_error = str(exc)
            logger.error("Modbus TCP connection exception to %s:%d: %s", self.host, self.port, exc)
            return False

    def disconnect(self) -> None:
        """Close TCP socket safely."""
        try:
            if self._client:
                self._client.close()
        except Exception as exc:
            logger.warning("Error during Modbus TCP disconnect: %s", exc)
        finally:
            self._connected = False
            self._client = None
            logger.info("Modbus TCP disconnected")

    def test_connection(self) -> Dict[str, Any]:
        """Perform a test connection."""
        start_t = time.monotonic()
        was_connected = self.is_connected
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
                "unit_id": self.unit_id,
                "protocol": "Modbus TCP",
            },
        }

    # -------------------------------------------------------------------------
    # Telemetry Polling (Strictly Read-Only)
    # -------------------------------------------------------------------------

    def read_points(
        self,
        points: List[PointDefinition],
        gateway_id: str = "plc-gw-01",
        device_id: str = "modbus_device",
        sequence_number: int = 0,
    ) -> NormalisedTelemetryBatch:
        """
        Poll allowlisted points using Modbus read function codes (FC 0x01, 0x02, 0x03, 0x04).
        NEVER issues write function codes.
        """
        start_t = time.monotonic()
        ts = utc_now_iso()

        if not self.is_connected or self._client is None:
            err = self._last_error or "Not connected to Modbus server"
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

        readings: Dict[str, NormalisedReading] = {}

        try:
            # Group points by table and sort by address
            parsed_points: List[Tuple[PointDefinition, ModbusAddress, int]] = []
            for pt in points:
                addr = parse_modbus_address(pt.device_address)
                # Word size: 2 registers for 32-bit types, 1 for 16-bit/bool
                reg_count = 2 if pt.data_type in (PointDataType.INT32, PointDataType.UINT32, PointDataType.FLOAT32) else 1
                parsed_points.append((pt, addr, reg_count))

            # Execute reads grouped by table
            for pt, addr, reg_count in parsed_points:
                try:
                    raw_val = self._read_single_point(addr, reg_count, pt.data_type)

                    readings[pt.point_id] = NormalisedReading.create_good(
                        point=pt,
                        raw_val=raw_val,
                        timestamp_utc=ts,
                    )
                except (ConnectionException, ModbusIOException, ConnectionError, OSError, TimeoutError) as c_exc:
                    self._connected = False
                    self._last_error = str(c_exc)
                    logger.warning("Modbus communication error on point '%s': %s", pt.point_id, c_exc)
                    raise
                except Exception as p_exc:
                    logger.debug("Failed reading point '%s' (%s): %s", pt.point_id, pt.device_address, p_exc)
                    if not self.is_connected:
                        self._connected = False
                        self._last_error = str(p_exc)
                        raise
                    readings[pt.point_id] = NormalisedReading.create_failure(
                        point_id=pt.point_id,
                        error_msg=str(p_exc),
                        quality=DataQuality.BAD,
                        timestamp_utc=ts,
                    )

            if not self.is_connected:
                raise ConnectionError(self._last_error or "Modbus TCP connection lost during poll")

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
            logger.error("Modbus read cycle failed: %s", exc)

            for pt in points:
                if pt.point_id not in readings or readings[pt.point_id].quality != DataQuality.GOOD:
                    readings[pt.point_id] = NormalisedReading.create_failure(
                        point_id=pt.point_id,
                        error_msg=str(exc),
                        quality=DataQuality.COMM_FAILURE,
                        timestamp_utc=ts,
                    )

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

    def _read_single_point(
        self,
        addr: ModbusAddress,
        count: int,
        data_type: PointDataType,
    ) -> Any:
        """Issue appropriate Modbus read function code for single point."""
        if not self._client or not self.is_connected:
            self._connected = False
            raise ConnectionError("Modbus client is not connected")

        # FC 0x01: Read Coils
        if addr.table == ModbusTable.COIL:
            res = self._client.read_coils(addr.offset, count=count, device_id=self.unit_id)
            if res.isError():
                if isinstance(res, (ConnectionException, ModbusIOException)) or (self._client and not self._client.connected):
                    self._connected = False
                    raise ConnectionError(f"Modbus connection error on FC01: {res}")
                raise RuntimeError(f"Modbus FC01 Error: {res}")
            return int(bool(res.bits[0]))

        # FC 0x02: Read Discrete Inputs
        if addr.table == ModbusTable.DISCRETE_INPUT:
            res = self._client.read_discrete_inputs(addr.offset, count=count, device_id=self.unit_id)
            if res.isError():
                if isinstance(res, (ConnectionException, ModbusIOException)) or (self._client and not self._client.connected):
                    self._connected = False
                    raise ConnectionError(f"Modbus connection error on FC02: {res}")
                raise RuntimeError(f"Modbus FC02 Error: {res}")
            return int(bool(res.bits[0]))

        # FC 0x03: Read Holding Registers
        if addr.table == ModbusTable.HOLDING_REGISTER:
            res = self._client.read_holding_registers(addr.offset, count=count, device_id=self.unit_id)
            if res.isError():
                if isinstance(res, (ConnectionException, ModbusIOException)) or (self._client and not self._client.connected):
                    self._connected = False
                    raise ConnectionError(f"Modbus connection error on FC03: {res}")
                raise RuntimeError(f"Modbus FC03 Error: {res}")
            regs = res.registers
            if addr.bit_index is not None:
                # Extract specific bit from register
                reg_val = regs[0]
                return int(bool((reg_val >> addr.bit_index) & 1))
            return _decode_registers(regs, data_type, self.word_order)

        # FC 0x04: Read Input Registers
        if addr.table == ModbusTable.INPUT_REGISTER:
            res = self._client.read_input_registers(addr.offset, count=count, device_id=self.unit_id)
            if res.isError():
                if isinstance(res, (ConnectionException, ModbusIOException)) or (self._client and not self._client.connected):
                    self._connected = False
                    raise ConnectionError(f"Modbus connection error on FC04: {res}")
                raise RuntimeError(f"Modbus FC04 Error: {res}")
            regs = res.registers
            if addr.bit_index is not None:
                reg_val = regs[0]
                return int(bool((reg_val >> addr.bit_index) & 1))
            return _decode_registers(regs, data_type, self.word_order)

        raise ValueError(f"Unsupported Modbus table: {addr.table}")

    # -------------------------------------------------------------------------
    # Diagnostics & Device Identification
    # -------------------------------------------------------------------------

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "adapter_id": self._metadata.adapter_id,
            "connected": self.is_connected,
            "host": self.host,
            "port": self.port,
            "unit_id": self.unit_id,
            "word_order": self.word_order,
            "packets_read": self._packets_read,
            "last_latency_ms": round(self._last_latency_ms, 2),
            "last_error": self._last_error,
        }

    def get_device_info(self) -> Dict[str, str]:
        return {
            "protocol": "Modbus TCP",
            "host": self.host,
            "port": str(self.port),
            "unit_id": str(self.unit_id),
        }

    # ── Read-Only Security Invariant Enforcement ──────────────────────────────
    def write(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Write operations are strictly prohibited on read-only adapter '{self._metadata.adapter_id}'."
        )

    def force_coil(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Forcing coils is strictly prohibited on read-only adapter '{self._metadata.adapter_id}'."
        )

    def write_register(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Writing registers is strictly prohibited on read-only adapter '{self._metadata.adapter_id}'."
        )

    def write_coils(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Writing multiple coils is strictly prohibited on read-only adapter '{self._metadata.adapter_id}'."
        )

    def write_registers(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly prohibited method. Raises ReadOnlySecurityViolation."""
        raise ReadOnlySecurityViolation(
            f"Writing multiple registers is strictly prohibited on read-only adapter '{self._metadata.adapter_id}'."
        )
