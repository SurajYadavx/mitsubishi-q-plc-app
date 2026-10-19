"""
tests/mock_modbus_server.py
───────────────────────────
Deterministic in-process Modbus TCP server fixture for automated integration tests.
Implements the Modbus Application Protocol (MBAP) over TCP for all four read function codes
(FC 0x01, 0x02, 0x03, 0x04) without external dependencies or live hardware.
"""

from __future__ import annotations

import logging
import select
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MockModbusServer:
    """
    Lightweight, deterministic Modbus TCP server running on localhost.
    Supports fault injection, exception responses, and write attempt detection.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.requested_port = port
        self.port: int = 0

        # Memory tables (0-based offsets)
        self.coils: Dict[int, bool] = {}
        self.discrete_inputs: Dict[int, bool] = {}
        self.holding_registers: Dict[int, int] = {}
        self.input_registers: Dict[int, int] = {}

        # Tracking and fault injection
        self.received_function_codes: List[int] = []
        self.write_requests_received: List[Dict[str, Any]] = []
        self.injected_exceptions: Dict[int, int] = {}  # fc -> exception_code
        self.inject_delay_sec: float = 0.0
        self.simulate_drop: bool = False

        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # -------------------------------------------------------------------------
    # Server Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> int:
        """Start server in background thread. Returns assigned port."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.requested_port))
        self._server_sock.listen(5)
        self.port = self._server_sock.getsockname()[1]
        self._running = True

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        time.sleep(0.05)  # Allow socket to listen
        logger.debug("MockModbusServer started on %s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        """Shut down server cleanly."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.debug("MockModbusServer stopped on port %d", self.port)

    # -------------------------------------------------------------------------
    # Value Setup Helpers
    # -------------------------------------------------------------------------

    def set_coil(self, offset: int, value: bool) -> None:
        self.coils[offset] = bool(value)

    def set_discrete_input(self, offset: int, value: bool) -> None:
        self.discrete_inputs[offset] = bool(value)

    def set_holding_register(self, offset: int, value: int) -> None:
        self.holding_registers[offset] = value & 0xFFFF

    def set_input_register(self, offset: int, value: int) -> None:
        self.input_registers[offset] = value & 0xFFFF

    def set_holding_registers_32bit(
        self,
        offset: int,
        val: Any,
        is_float: bool = False,
        word_order: str = "big_endian",
    ) -> None:
        """Convenience helper to set a 32-bit int or float across two consecutive holding registers."""
        if is_float:
            raw_bytes = struct.pack(">f", float(val))
        else:
            raw_bytes = struct.pack(">i", int(val))

        w0, w1 = struct.unpack(">HH", raw_bytes)
        if word_order.lower() in ("little_endian", "word_swap"):
            self.set_holding_register(offset, w1)
            self.set_holding_register(offset + 1, w0)
        else:
            self.set_holding_register(offset, w0)
            self.set_holding_register(offset + 1, w1)

    # -------------------------------------------------------------------------
    # Request Processing Loop
    # -------------------------------------------------------------------------

    def _run_loop(self) -> None:
        while self._running:
            try:
                r, _, _ = select.select([self._server_sock], [], [], 0.2)
                if not r or not self._running:
                    continue
                conn, _ = self._server_sock.accept()
                client_thread = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                client_thread.start()
            except Exception:
                break

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        try:
            while self._running:
                # Read 7-byte MBAP header
                header = conn.recv(7)
                if not header or len(header) < 7:
                    break

                tid, pid, length, uid = struct.unpack(">HHHB", header)
                # Remaining pdu length is length - 1 (since length includes uid)
                pdu_len = length - 1
                pdu = b""
                while len(pdu) < pdu_len:
                    chunk = conn.recv(pdu_len - len(pdu))
                    if not chunk:
                        break
                    pdu += chunk

                if len(pdu) < pdu_len:
                    break

                fc = pdu[0]
                self.received_function_codes.append(fc)

                # Simulate dropped connection
                if self.simulate_drop:
                    conn.close()
                    return

                # Simulate communication delay / timeout
                if self.inject_delay_sec > 0:
                    time.sleep(self.inject_delay_sec)

                # Check for write attempts (FC 0x05, 0x06, 0x0F, 0x10)
                if fc in (0x05, 0x06, 0x0F, 0x10):
                    self.write_requests_received.append({
                        "fc": fc,
                        "pdu": pdu.hex(),
                        "time": time.monotonic(),
                    })
                    # Reject write attempt with Illegal Function exception
                    resp_pdu = struct.pack(">BB", fc | 0x80, 0x01)
                    mbap = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, uid)
                    conn.sendall(mbap + resp_pdu)
                    continue

                # Check injected exception
                if fc in self.injected_exceptions:
                    exc_code = self.injected_exceptions[fc]
                    resp_pdu = struct.pack(">BB", fc | 0x80, exc_code)
                    mbap = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, uid)
                    conn.sendall(mbap + resp_pdu)
                    continue

                # Process Read Commands
                resp_pdu = self._process_read(fc, pdu[1:])
                mbap = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, uid)
                conn.sendall(mbap + resp_pdu)

        except Exception as exc:
            logger.debug("Client socket closed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _process_read(self, fc: int, pdu_payload: bytes) -> bytes:
        if len(pdu_payload) < 4:
            return struct.pack(">BB", fc | 0x80, 0x03)  # Illegal data value

        addr, qty = struct.unpack(">HH", pdu_payload[:4])

        # FC 0x01: Read Coils
        if fc == 0x01:
            bits = [int(self.coils.get(addr + i, False)) for i in range(qty)]
            byte_count = (qty + 7) // 8
            data = bytearray(byte_count)
            for i, bit in enumerate(bits):
                if bit:
                    data[i // 8] |= (1 << (i % 8))
            return struct.pack(">BB", fc, byte_count) + bytes(data)

        # FC 0x02: Read Discrete Inputs
        if fc == 0x02:
            bits = [int(self.discrete_inputs.get(addr + i, False)) for i in range(qty)]
            byte_count = (qty + 7) // 8
            data = bytearray(byte_count)
            for i, bit in enumerate(bits):
                if bit:
                    data[i // 8] |= (1 << (i % 8))
            return struct.pack(">BB", fc, byte_count) + bytes(data)

        # FC 0x03: Read Holding Registers
        if fc == 0x03:
            byte_count = qty * 2
            data = bytearray()
            for i in range(qty):
                reg_val = self.holding_registers.get(addr + i, 0)
                data.extend(struct.pack(">H", reg_val & 0xFFFF))
            return struct.pack(">BB", fc, byte_count) + bytes(data)

        # FC 0x04: Read Input Registers
        if fc == 0x04:
            byte_count = qty * 2
            data = bytearray()
            for i in range(qty):
                reg_val = self.input_registers.get(addr + i, 0)
                data.extend(struct.pack(">H", reg_val & 0xFFFF))
            return struct.pack(">BB", fc, byte_count) + bytes(data)

        # Unknown function code
        return struct.pack(">BB", fc | 0x80, 0x01)
