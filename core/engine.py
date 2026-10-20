"""
core/engine.py
──────────────
Multi-Device Polling Engine and Orchestrator for the Universal PLC Platform.
Coordinates independent device polling lifecycles, error isolation, telemetry
normalization, and transport dispatching.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.adapter_interface import IPlcProtocolAdapter
from core.adapter_registry import AdapterRegistry, default_adapter_registry
from core.config_loader import DeviceConfig, GatewayConfig
from core.models import NormalisedTelemetryBatch, utc_now_iso
from core.transport_interface import ITelemetryTransport, SerialTelemetryTransport

logger = logging.getLogger(__name__)


@dataclass
class DeviceState:
    """Runtime tracking state for an individual monitored device."""
    config: DeviceConfig
    adapter: IPlcProtocolAdapter
    connected: bool = False
    last_poll_mono: float = 0.0
    consecutive_failures: int = 0
    total_polls: int = 0
    last_batch: Optional[NormalisedTelemetryBatch] = None


class GatewayEngine:
    """
    Universal Gateway Orchestrator.
    Manages multiple independent PLC adapters and dispatches telemetry to configured transports.
    """

    def __init__(
        self,
        config: GatewayConfig,
        registry: Optional[AdapterRegistry] = None,
        transport: Optional[ITelemetryTransport] = None,
    ) -> None:
        self.config = config
        self.registry = registry or default_adapter_registry
        self.gateway_id = config.id
        self.heartbeat_sec = config.heartbeat_sec
        self.poll_interval_sec = config.poll_interval_sec

        # Telemetry Transport (default to Serial USB transport matching existing hardware)
        self.transport = transport or SerialTelemetryTransport(
            port=config.serial_port,
            baud_rate=config.serial_baud,
            timeout=config.serial_timeout,
            gateway_id=config.id,
            ack_timeout=getattr(config, "serial_ack_timeout", 3.0),
        )

        self._devices: Dict[str, DeviceState] = {}
        self._sequence_number: int = 0
        self._running: bool = False
        self._last_heartbeat_mono: float = 0.0

        # Initialize adapters for all enabled devices
        self._init_devices()

    def _init_devices(self) -> None:
        """Instantiate protocol adapters for enabled devices from configuration."""
        for dev_cfg in self.config.get_enabled_devices():
            try:
                adapter = self.registry.create_adapter(
                    adapter_id=dev_cfg.adapter_id,
                    connection_params=dev_cfg.connection,
                )
                self._devices[dev_cfg.id] = DeviceState(
                    config=dev_cfg,
                    adapter=adapter,
                )
                logger.info(
                    "Device registered: id='%s' name='%s' adapter='%s'",
                    dev_cfg.id, dev_cfg.name, adapter.metadata.display_name
                )
            except Exception as exc:
                logger.error("Failed to initialize device '%s': %s", dev_cfg.id, exc)

    @property
    def active_devices(self) -> Dict[str, DeviceState]:
        return self._devices

    # -------------------------------------------------------------------------
    # Lifecycle Control
    # -------------------------------------------------------------------------

    def start(self) -> bool:
        """Initialize transports and connect devices."""
        logger.info("═══ Starting Universal Gateway Engine (id=%s) ═══", self.gateway_id)
        self._running = True
        self._last_heartbeat_mono = time.monotonic()

        # Open transport (non-fatal if serial port is momentarily unplugged)
        if not self.transport.open():
            logger.warning("Transport unavailable at startup – will retry during loop")

        # Connect initial devices
        for dev_id, state in self._devices.items():
            self._connect_device(dev_id, state)

        return True

    def stop(self) -> None:
        """Graceful shutdown of all adapters and transports."""
        logger.info("═══ Stopping Universal Gateway Engine ═══")
        self._running = False

        # Disconnect all devices
        for dev_id, state in self._devices.items():
            try:
                if state.connected:
                    state.adapter.disconnect()
                    state.connected = False
            except Exception as exc:
                logger.warning("Error disconnecting device '%s': %s", dev_id, exc)

        # Emit shutdown heartbeat and close transport
        try:
            self.transport.publish_heartbeat(
                connected=False,
                error="Gateway Engine Shutdown",
                gateway_id=self.gateway_id,
            )
            self.transport.close()
        except Exception as exc:
            logger.warning("Error closing transport during shutdown: %s", exc)

        logger.info("═══ Universal Gateway Engine Stopped Cleanly ═══")

    def _connect_device(self, dev_id: str, state: DeviceState) -> bool:
        """Attempt connection to device with error logging."""
        try:
            ok = state.adapter.connect()
            state.connected = ok
            if ok:
                state.consecutive_failures = 0
                logger.info("Device '%s' connected successfully (%s)", dev_id, state.config.name)
            else:
                state.consecutive_failures += 1
                logger.warning(
                    "Device '%s' connection failed (attempt %d)",
                    dev_id, state.consecutive_failures
                )
            return ok
        except Exception as exc:
            state.connected = False
            state.consecutive_failures += 1
            logger.error("Exception connecting device '%s': %s", dev_id, exc)
            return False

    # -------------------------------------------------------------------------
    # Single Step / Poll Cycle (Deterministic for testing)
    # -------------------------------------------------------------------------

    def poll_once(self) -> List[NormalisedTelemetryBatch]:
        """
        Execute one poll iteration across all active devices.
        Returns the list of generated telemetry batches.
        Failure in one device is completely isolated and does not affect others.
        """
        now_mono = time.monotonic()
        batches: List[NormalisedTelemetryBatch] = []

        # 1. Re-open transport if it dropped
        if not self.transport.is_open:
            self.transport.open()

        # 2. Poll each enabled device according to its rate
        for dev_id, state in self._devices.items():
            dev_poll_sec = state.config.poll_interval_sec
            elapsed = now_mono - state.last_poll_mono

            if elapsed < dev_poll_sec and state.total_polls > 0:
                continue  # Not due yet for this device

            state.last_poll_mono = now_mono
            state.total_polls += 1
            self._sequence_number += 1

            # Auto-reconnect if disconnected
            if not state.connected:
                self._connect_device(dev_id, state)

            # Read configured points
            try:
                batch = state.adapter.read_points(
                    points=state.config.points,
                    gateway_id=self.gateway_id,
                    device_id=dev_id,
                    sequence_number=self._sequence_number,
                )
            except Exception as exc:
                logger.error("Unexpected exception polling device '%s': %s", dev_id, exc)
                state.connected = False
                state.consecutive_failures += 1
                batch = NormalisedTelemetryBatch(
                    gateway_id=self.gateway_id,
                    device_id=dev_id,
                    sequence_number=self._sequence_number,
                    timestamp_utc=utc_now_iso(),
                    connected=False,
                    error=str(exc),
                )

            state.connected = batch.connected
            state.last_batch = batch
            batches.append(batch)

            # Dispatch to transport
            try:
                self.transport.publish_batch(
                    batch=batch,
                    point_definitions=state.config.point_map,
                )
            except Exception as exc:
                logger.error("Transport failed to publish batch for device '%s': %s", dev_id, exc)

            # Log poll status
            if batch.connected:
                logger.debug(
                    "Poll OK device='%s' points=%d duration=%.1fms",
                    dev_id, len(batch.readings), batch.scan_duration_ms
                )
            else:
                logger.warning("Poll FAILED device='%s': %s", dev_id, batch.error)

        # 3. Handle periodic heartbeat
        if now_mono - self._last_heartbeat_mono >= self.heartbeat_sec:
            any_connected = any(s.connected for s in self._devices.values())
            self.transport.publish_heartbeat(
                connected=any_connected,
                gateway_id=self.gateway_id,
            )
            self._last_heartbeat_mono = now_mono

        return batches

    # -------------------------------------------------------------------------
    # Main Continuous Run Loop
    # -------------------------------------------------------------------------

    def run_loop(self) -> None:
        """Run continuous polling loop with graceful loop pacing."""
        self.start()
        try:
            while self._running:
                loop_start = time.monotonic()
                self.poll_once()

                # Sleep to pace loop
                elapsed = time.monotonic() - loop_start
                sleep_sec = max(0.05, self.poll_interval_sec - elapsed)
                time.sleep(sleep_sec)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received in gateway engine.")
        finally:
            self.stop()
