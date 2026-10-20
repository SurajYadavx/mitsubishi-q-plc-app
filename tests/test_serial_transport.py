"""
tests/test_serial_transport.py
───────────────────────────────
Comprehensive unit tests for SerialTelemetryTransport stop-and-wait flow control,
acknowledgement parsing, bounded retries, duplicate handling, NACK handling,
thread-safe transaction serialization, and payload schema integrity.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

from core.models import (
    DataQuality,
    NormalisedReading,
    NormalisedTelemetryBatch,
    PointDataType,
    PointDefinition,
    utc_now_iso,
)
from core.transport_interface import SerialTelemetryTransport


class FakeSerialPort:
    """
    In-memory mock for PySerial Serial object.
    Supports controllable read queues, write hooks, and thread safety.
    """

    def __init__(self) -> None:
        self.is_open = True
        self.written_chunks: List[bytes] = []
        self._rx_queue = bytearray()
        self._lock = threading.Lock()
        self.on_write_cb: Optional[Callable[[bytes, FakeSerialPort], None]] = None

    def write(self, data: bytes) -> int:
        with self._lock:
            self.written_chunks.append(data)
            cb = self.on_write_cb
        if cb:
            cb(data, self)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        with self._lock:
            self.is_open = False

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._rx_queue)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            chunk = bytes(self._rx_queue[:size])
            del self._rx_queue[:size]
            return chunk

    def inject_rx(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self._lock:
            self._rx_queue.extend(data)


def create_sample_batch(device_id: str = "melsec_q03") -> NormalisedTelemetryBatch:
    readings = {
        "X0": NormalisedReading(
            point_id="X0",
            raw_value=1,
            engineering_value=1,
            quality=DataQuality.GOOD,
        ),
        "X1": NormalisedReading(
            point_id="X1",
            raw_value=0,
            engineering_value=0,
            quality=DataQuality.GOOD,
        ),
        "Y20": NormalisedReading(
            point_id="Y20",
            raw_value=1,
            engineering_value=1,
            quality=DataQuality.GOOD,
        ),
    }
    return NormalisedTelemetryBatch(
        gateway_id="plc-gw-01",
        device_id=device_id,
        sequence_number=1,
        timestamp_utc=utc_now_iso(),
        readings=readings,
        connected=True,
    )


class TestSerialFlowControlProtocol(unittest.TestCase):
    """Test suite covering the 8 mandatory serial flow control acceptance criteria."""

    def setUp(self) -> None:
        self.fake_ser = FakeSerialPort()
        self.transport = SerialTelemetryTransport(
            port="COM10",
            baud_rate=115200,
            ack_timeout=0.2,  # Fast timeout for unit testing
            max_retries=1,
            gateway_id="plc-gw-01",
        )
        self.transport._ser = self.fake_ser

    def test_serial_read_exception_closes_without_deadlock(self) -> None:
        class FailingReadPort(FakeSerialPort):
            @property
            def in_waiting(self) -> int:
                return 1

            def read(self, size: int = 1) -> bytes:
                raise OSError("simulated serial read failure")

        self.transport._ser = FailingReadPort()
        with self.assertLogs("core.transport_interface", level="ERROR"):
            result = self.transport.publish_batch(create_sample_batch())

        self.assertFalse(result)
        self.assertFalse(self.transport.is_open)
        self.assertIsNone(self.transport._ser)

    def test_serial_write_exception_closes_without_deadlock(self) -> None:
        def fail_write(data: bytes) -> int:
            raise OSError("simulated serial write failure")

        self.fake_ser.write = fail_write

        with self.assertLogs("core.transport_interface", level="ERROR"):
            result = self.transport.publish_batch(create_sample_batch())

        self.assertFalse(result)
        self.assertFalse(self.transport.is_open)
        self.assertIsNone(self.transport._ser)


    # -------------------------------------------------------------------------
    # Criterion 1: A matching ACK completes the transaction.
    # -------------------------------------------------------------------------
    def test_criterion_1_matching_ack_completes_transaction(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            parsed = json.loads(data.decode("utf-8").strip())
            ack_line = f"@ACK|{parsed['type']}|{parsed['seq']}\n"
            port.inject_rx(ack_line)

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertTrue(success, "Matching ACK must complete the transaction with True")
        self.assertEqual(len(self.fake_ser.written_chunks), 1, "Exactly one frame must be written")
        payload = json.loads(self.fake_ser.written_chunks[0].decode("utf-8").strip())
        self.assertEqual(payload["type"], "plc_reading")
        self.assertEqual(payload["seq"], 1)

    # -------------------------------------------------------------------------
    # Criterion 2: Debug output arriving before an ACK does not break transaction.
    # -------------------------------------------------------------------------
    def test_criterion_2_debug_output_before_ack_is_handled(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            parsed = json.loads(data.decode("utf-8").strip())
            # Inject ESP8266 boot and diagnostic messages prior to the control ACK
            port.inject_rx("[SYS] PLC Monitor firmware starting...\n")
            port.inject_rx("[WiFi] Connected, IP: 192.168.3.150\n")
            port.inject_rx("[MQTT] Published telemetry seq=" + str(parsed["seq"]) + "\n")
            port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        with self.assertLogs("core.transport_interface", level="INFO") as log_ctx:
            success = self.transport.publish_batch(batch)

        self.assertTrue(success, "Transaction must succeed despite intervening debug messages")
        # Verify debug lines were logged as [ESP8266]
        debug_logs = [record for record in log_ctx.output if "[ESP8266]" in record]
        self.assertTrue(any("Connected, IP:" in rec for rec in debug_logs))
        self.assertTrue(any("Published telemetry seq=" in rec for rec in debug_logs))

    # -------------------------------------------------------------------------
    # Criterion 3: An ACK with the wrong sequence or message type is ignored.
    # -------------------------------------------------------------------------
    def test_criterion_3_mismatched_ack_ignored_until_matching_ack(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            parsed = json.loads(data.decode("utf-8").strip())
            # Inject wrong message type and wrong sequence numbers
            port.inject_rx("@ACK|heartbeat|999\n")
            port.inject_rx(f"@ACK|{parsed['type']}|999\n")
            port.inject_rx(f"@ACK|other_type|{parsed['seq']}\n")
            # Finally emit matching ACK
            port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertTrue(success, "Matching ACK after mismatched ACKs must succeed")
        self.assertEqual(len(self.fake_ser.written_chunks), 1, "Should not retry if matching ACK arrives")

    def test_criterion_3b_only_mismatched_acks_times_out(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            # Only ever return wrong sequence ACK
            port.inject_rx("@ACK|plc_reading|8888\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertFalse(success, "Mismatched ACKs must not satisfy the transaction; should fail after retry")
        # 1 original attempt + 1 retry = 2 writes
        self.assertEqual(len(self.fake_ser.written_chunks), 2)

    # -------------------------------------------------------------------------
    # Criterion 4: A missing ACK causes exactly one retry using the same frame and identifiers.
    # -------------------------------------------------------------------------
    def test_criterion_4_missing_ack_causes_exactly_one_retry_same_identifiers(self) -> None:
        write_count = 0

        def on_write(data: bytes, port: FakeSerialPort) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                # Respond only to the second (retried) frame
                parsed = json.loads(data.decode("utf-8").strip())
                port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertTrue(success, "Retry must succeed when second attempt receives ACK")
        self.assertEqual(len(self.fake_ser.written_chunks), 2, "Must retry exactly once (total 2 writes)")

        frame1 = json.loads(self.fake_ser.written_chunks[0].decode("utf-8").strip())
        frame2 = json.loads(self.fake_ser.written_chunks[1].decode("utf-8").strip())
        self.assertEqual(frame1["seq"], frame2["seq"], "Sequence number MUST remain identical on retry")
        self.assertEqual(frame1["type"], frame2["type"], "Type MUST remain identical on retry")
        self.assertEqual(frame1["gw_id"], frame2["gw_id"], "Gateway ID MUST remain identical on retry")
        self.assertEqual(frame1["ts"], frame2["ts"], "Timestamp MUST remain identical on retry")
        self.assertEqual(self.fake_ser.written_chunks[0], self.fake_ser.written_chunks[1],
                         "Serialized frame bytes MUST be byte-for-byte identical")

    # -------------------------------------------------------------------------
    # Criterion 5: Retransmitted duplicate frame preserves identical frame data.
    # -------------------------------------------------------------------------
    def test_criterion_5_duplicate_retransmission_identifiers_preserved(self) -> None:
        """Verify gateway guarantees stop-and-wait duplicate frame identity."""
        self.transport.ack_timeout = 0.05
        # No ACK injected -> fails after retry
        success = self.transport.publish_heartbeat(connected=True)
        self.assertFalse(success)
        self.assertEqual(len(self.fake_ser.written_chunks), 2)
        p1 = json.loads(self.fake_ser.written_chunks[0].decode("utf-8").strip())
        p2 = json.loads(self.fake_ser.written_chunks[1].decode("utf-8").strip())
        self.assertEqual(p1["type"], "heartbeat")
        self.assertEqual(p1["seq"], p2["seq"])
        self.assertEqual(p1["connected"], p2["connected"])

    # -------------------------------------------------------------------------
    # Criterion 6: NACKs and timeouts are surfaced as transport failures.
    # -------------------------------------------------------------------------
    def test_criterion_6a_nack_causes_immediate_failure_without_retry(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            parsed = json.loads(data.decode("utf-8").strip())
            port.inject_rx(f"@NACK|{parsed['type']}|{parsed['seq']}|invalid_schema\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertFalse(success, "NACK must cause transaction failure")
        self.assertEqual(len(self.fake_ser.written_chunks), 1, "NACK must abort without blind retry")

    def test_criterion_6b_proto_err_and_timeout_surfaced_as_failure(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            port.inject_rx("@PROTO_ERR|buffer_overflow\n")

        self.fake_ser.on_write_cb = on_write
        self.transport.ack_timeout = 0.05
        batch = create_sample_batch()
        success = self.transport.publish_batch(batch)

        self.assertFalse(success, "Protocol error must surface as failure after retries exhausted")
        self.assertEqual(len(self.fake_ser.written_chunks), 2, "Must perform exactly one retry on protocol error")

    def test_criterion_6c_proto_err_triggers_prompt_bounded_recovery_succeeding_on_ack(self) -> None:
        """Verify @PROTO_ERR triggers immediate retry without waiting for ack_timeout."""
        write_count = 0

        def on_write(data: bytes, port: FakeSerialPort) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 1:
                # First attempt fails with protocol error (e.g. framing error)
                port.inject_rx("@PROTO_ERR|missing_identifiers\n")
            elif write_count == 2:
                # Retried attempt succeeds with matching ACK
                parsed = json.loads(data.decode("utf-8").strip())
                port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        self.fake_ser.on_write_cb = on_write
        self.transport.ack_timeout = 2.0  # Deliberately large timeout
        batch = create_sample_batch()

        t_start = time.monotonic()
        success = self.transport.publish_batch(batch)
        elapsed = time.monotonic() - t_start

        self.assertTrue(success, "Transaction must succeed after prompt retry on @PROTO_ERR")
        self.assertEqual(len(self.fake_ser.written_chunks), 2, "Must retry exactly once")
        self.assertLess(elapsed, 0.5, "Retry must be prompt and not wait for 2.0s ACK timeout")
        self.assertEqual(self.fake_ser.written_chunks[0], self.fake_ser.written_chunks[1],
                         "Retried frame must be byte-for-byte identical")

    def test_criterion_6d_proto_err_exhausts_retries_promptly(self) -> None:
        """Verify repeated @PROTO_ERR fails promptly without waiting for multi-second timeouts."""
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            port.inject_rx("@PROTO_ERR|buffer_overflow\n")

        self.fake_ser.on_write_cb = on_write
        self.transport.ack_timeout = 2.0  # Large timeout
        batch = create_sample_batch()

        t_start = time.monotonic()
        success = self.transport.publish_batch(batch)
        elapsed = time.monotonic() - t_start

        self.assertFalse(success, "Repeated protocol error must fail transaction")
        self.assertEqual(len(self.fake_ser.written_chunks), 2, "Must perform bounded retry (1 initial + 1 retry)")
        self.assertLess(elapsed, 0.5, "Must fail promptly rather than waiting 2 * 2.0s = 4.0s")

    # -------------------------------------------------------------------------
    # Criterion 7: Multiple concurrent callers cannot interleave serial transactions.
    # -------------------------------------------------------------------------
    def test_criterion_7_concurrent_threads_cannot_interleave_transactions(self) -> None:
        active_in_flight = 0
        max_concurrent = 0
        counter_lock = threading.Lock()

        def on_write(data: bytes, port: FakeSerialPort) -> None:
            nonlocal active_in_flight, max_concurrent
            with counter_lock:
                active_in_flight += 1
                if active_in_flight > max_concurrent:
                    max_concurrent = active_in_flight

            # Sleep slightly to expose interleaving race conditions if lock is absent
            time.sleep(0.01)
            parsed = json.loads(data.decode("utf-8").strip())
            port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

            with counter_lock:
                active_in_flight -= 1

        self.fake_ser.on_write_cb = on_write

        results: List[bool] = []
        threads: List[threading.Thread] = []

        def worker(thread_idx: int) -> None:
            if thread_idx % 2 == 0:
                ok = self.transport.publish_batch(create_sample_batch())
            else:
                ok = self.transport.publish_heartbeat(connected=True)
            results.append(ok)

        for i in range(8):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(results), 8)
        self.assertTrue(all(results), "All concurrent transactions must succeed")
        self.assertEqual(max_concurrent, 1, "Transactions MUST be strictly serialized (max concurrency == 1)")
        self.assertEqual(len(self.fake_ser.written_chunks), 8)

    # -------------------------------------------------------------------------
    # Criterion 8: Existing payload JSON and MQTT topic formats are unchanged.
    # -------------------------------------------------------------------------
    def test_criterion_8_payload_json_schema_preserved(self) -> None:
        def on_write(data: bytes, port: FakeSerialPort) -> None:
            parsed = json.loads(data.decode("utf-8").strip())
            port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        self.fake_ser.on_write_cb = on_write
        batch = create_sample_batch()
        self.transport.publish_batch(batch)

        raw = self.fake_ser.written_chunks[0].decode("utf-8")
        self.assertTrue(raw.endswith("\n"), "Must be newline-terminated")
        payload = json.loads(raw.strip())

        # Exact required telemetry schema keys
        required_keys = {"type", "gw_id", "seq", "ts", "connected", "error", "inputs", "outputs"}
        for k in required_keys:
            self.assertIn(k, payload, f"Mandatory telemetry key '{k}' must be present in JSON")

        self.assertEqual(payload["type"], "plc_reading")
        self.assertEqual(payload["gw_id"], "plc-gw-01")
        self.assertIsInstance(payload["seq"], int)
        self.assertIsInstance(payload["ts"], str)
        self.assertIsInstance(payload["inputs"], dict)
        self.assertIsInstance(payload["outputs"], dict)

    # -------------------------------------------------------------------------
    # ACK Timeout Safety and Bounded Configuration Verification
    # -------------------------------------------------------------------------
    def test_ack_timeout_safety_and_bounded_configuration(self) -> None:
        """
        Verify that:
        1. Default ack_timeout is 5.0s, safely exceeding firmware TLS socket timeout (2.0s).
        2. Responses arriving within bounded timeout succeed without retries.
        3. Custom configuration overrides are strictly propagated.
        """
        default_transport = SerialTelemetryTransport(port="COM9")
        self.assertEqual(default_transport.ack_timeout, 5.0,
                         "Default transport ACK timeout must be 5.0s to exceed 2.0s firmware socket timeout with margin")

        # Custom timeout propagation
        custom_transport = SerialTelemetryTransport(port="COM9", ack_timeout=4.5)
        self.assertEqual(custom_transport.ack_timeout, 4.5)

        # Timed response within deadline
        custom_port = FakeSerialPort()
        custom_transport._ser = custom_port

        def delayed_ack(data: bytes, port: FakeSerialPort) -> None:
            time.sleep(0.02)
            parsed = json.loads(data.decode("utf-8").strip())
            port.inject_rx(f"@ACK|{parsed['type']}|{parsed['seq']}\n")

        custom_port.on_write_cb = delayed_ack
        custom_transport.ack_timeout = 0.1
        success = custom_transport.publish_heartbeat(connected=True)
        self.assertTrue(success, "Response within timeout must succeed")
        self.assertEqual(len(custom_port.written_chunks), 1, "Must complete on attempt 1 without retry")


if __name__ == "__main__":
    unittest.main()
