
"""
plc_reader.py
─────────────
READ-ONLY interface to the Mitsubishi Q03UDECPU via MC Protocol (3E-frame/TCP).

This module ONLY issues batch-read commands (commandtype "read").
It contains no write, force, or remote-run commands and must remain that way.

Supported devices
  • X0–X7   (bit inputs,  octal address)
  • Y20–Y27 (bit outputs, octal address)

The existing X3 → Y20 ladder rung is preserved because this module never
touches the output side of the PLC.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pymcprotocol  # pip install pymcprotocol

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeviceReading:
    """One complete poll of all monitored I/O."""
    timestamp_iso: str = ""
    inputs: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, int] = field(default_factory=dict)
    connected: bool = False
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Device address helpers
# ─────────────────────────────────────────────────────────────────────────────

def _device_to_addr(device_str: str) -> tuple[str, int]:
    """
    Parse a device string like 'X3' or 'Y20' into (device_type, decimal_address).

    Mitsubishi Q-series X/Y devices use hexadecimal numbering (base 16) in GX Works2
    and MC Protocol (unlike legacy FX series which used octal).
    For example: X0-X7 are 0-7, Y20 is 0x20 = 32 decimal, Y27 is 0x27 = 39 decimal.
    """
    dev_type = device_str[0].upper()
    dev_num_str = device_str[1:]
    # Q-series X, Y, B, W devices are hexadecimal; M, D, R, etc. are decimal
    base = 16 if dev_type in ("X", "Y", "B", "W") else 10
    try:
        addr = int(dev_num_str, base)
    except ValueError:
        raise ValueError(f"Cannot parse device address from '{device_str}'")
    return dev_type, addr


def _addr_to_label(dev_type: str, addr_decimal: int) -> str:
    """Return a canonical label like 'X3' or 'Y20' using appropriate base representation."""
    if dev_type in ("X", "Y", "B", "W"):
        return f"{dev_type}{hex(addr_decimal)[2:].upper()}"
    return f"{dev_type}{addr_decimal}"


# ─────────────────────────────────────────────────────────────────────────────
# PLCReader class
# ─────────────────────────────────────────────────────────────────────────────

class PLCReader:
    """
    Manages a persistent 3E-frame MC Protocol TCP connection to the Q03UDECPU.

    Usage
    -----
    reader = PLCReader(ip="192.168.3.39", port=5010, timeout=3.0)
    reader.connect()
    reading = reader.poll()
    reader.disconnect()
    """

    # ------------------------------------------------------------------
    def __init__(
        self,
        ip: str,
        port: int,
        timeout: float = 3.0,
        input_devices: list[str] | None = None,
        output_devices: list[str] | None = None,
    ) -> None:
        self.ip = ip
        self.port = port
        self.timeout = timeout

        self.input_devices: list[str] = input_devices or [
            "X0", "X1", "X2", "X3", "X4", "X5", "X6", "X7"
        ]
        self.output_devices: list[str] = output_devices or [
            "Y20", "Y21", "Y22", "Y23", "Y24", "Y25", "Y26", "Y27"
        ]

        self._client: Optional[pymcprotocol.Type3E] = None
        self._connected: bool = False

    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """
        Open a TCP connection to the PLC.
        Returns True on success, False on failure (logged).
        """
        try:
            self._client = pymcprotocol.Type3E()
            timer_int = max(1, int(round(self.timeout)))
            self._client.setaccessopt(commtype="binary", timer_sec=timer_int)
            self._client.connect(self.ip, self.port)
            self._connected = True
            logger.info("PLC connected  ip=%s  port=%d (timeout=%ds)", self.ip, self.port, timer_int)
            return True
        except Exception as exc:
            self._connected = False
            self._client = None
            logger.error("PLC connect failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    def disconnect(self) -> None:
        """Cleanly close the TCP socket."""
        try:
            if self._client and getattr(self._client, "_is_connected", False):
                self._client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PLC disconnect error (ignored): %s", exc)
        finally:
            self._connected = False
            self._client = None
            logger.info("PLC disconnected")

    # ------------------------------------------------------------------
    def poll(self) -> DeviceReading:
        """
        READ all configured input and output device bits from the PLC.

        Returns a DeviceReading.  If the read fails the reading will have
        connected=False and an error message; the caller decides on retry.

        THIS METHOD NEVER ISSUES A WRITE COMMAND.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"

        if not self._connected or self._client is None:
            return DeviceReading(
                timestamp_iso=ts,
                connected=False,
                error="Not connected to PLC",
            )

        try:
            inputs_result = self._read_bit_devices(self.input_devices)
            outputs_result = self._read_bit_devices(self.output_devices)

            reading = DeviceReading(
                timestamp_iso=ts,
                inputs=inputs_result,
                outputs=outputs_result,
                connected=True,
            )
            logger.debug("Poll OK  inputs=%s  outputs=%s", inputs_result, outputs_result)
            return reading

        except Exception as exc:
            self._connected = False
            logger.error("Poll failed – marking disconnected: %s", exc)
            return DeviceReading(
                timestamp_iso=ts,
                connected=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    def _read_bit_devices(self, device_list: list[str]) -> dict[str, int]:
        """
        Read a list of individual bit devices.

        pymcprotocol batchread_bitunits reads a contiguous range.  Because
        X and Y devices are separate types, and because we have specific
        device numbers (X0-X7, Y20-Y27) we read each group contiguously.

        Returns dict mapping device label → 0 or 1.
        """
        if not device_list:
            return {}

        # Group by device type so we can batch within a type
        groups: dict[str, list[int]] = {}
        for dev_str in device_list:
            dev_type, addr = _device_to_addr(dev_str)
            groups.setdefault(dev_type, []).append(addr)

        result: dict[str, int] = {}
        for dev_type, addrs in groups.items():
            addrs_sorted = sorted(addrs)
            start_addr = addrs_sorted[0]
            count = addrs_sorted[-1] - start_addr + 1

            # Build the device head string that pymcprotocol expects (e.g. 'X0', 'Y20')
            head_device = _addr_to_label(dev_type, start_addr)

            raw: list[int] = self._client.batchread_bitunits(
                headdevice=head_device, readsize=count
            )

            for offset, val in enumerate(raw):
                addr_label = _addr_to_label(dev_type, start_addr + offset)
                if addr_label in device_list:
                    result[addr_label] = int(bool(val))

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Quick smoke-test (run directly: python plc_reader.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    reader = PLCReader(ip="192.168.3.39", port=5010, timeout=3.0)
    if reader.connect():
        for i in range(3):
            r = reader.poll()
            print(json.dumps(r.__dict__, indent=2))
            time.sleep(1)
        reader.disconnect()
    else:
        print("Could not connect – check IP/port and that PLC is reachable.")
