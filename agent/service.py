"""
agent/service.py
────────────────
LocalAgentService orchestrates GatewayEngine lifecycle and serves thread-safe
cached telemetry to API route handlers.
Ensures API requests NEVER duplicate polling loops or slow down protocol scanning.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from core.adapter_interface import IPlcProtocolAdapter
from core.adapter_registry import AdapterRegistry, default_adapter_registry
from core.config_loader import DeviceConfig, GatewayConfig
from core.engine import DeviceState, GatewayEngine
from core.models import DataQuality, NormalisedTelemetryBatch, utc_now_iso
from core.transport_interface import ITelemetryTransport, MockTelemetryTransport
from agent.security import redact_secrets

logger = logging.getLogger(__name__)


class LocalAgentService:
    """
    Manages the lifecycle of the Universal GatewayEngine and exposes thread-safe
    data snapshots for the Local Agent API.
    """

    def __init__(
        self,
        config: GatewayConfig,
        registry: Optional[AdapterRegistry] = None,
        transport: Optional[ITelemetryTransport] = None,
        simulation_mode: bool = False,
    ) -> None:
        self.config = config
        self.registry = registry or default_adapter_registry
        self.simulation_mode = simulation_mode

        # If simulation mode is active, map all devices to simulated adapter
        if self.simulation_mode:
            for dev in self.config.devices:
                dev.adapter_id = "simulated"
                dev.enabled = True
            logger.info("LocalAgentService initialized in SIMULATION mode.")

        # Transport: default to MockTelemetryTransport in simulation or if unspecified
        self.transport = transport or MockTelemetryTransport(gateway_id=config.id)

        # Initialize core polling engine
        self.engine = GatewayEngine(
            config=self.config,
            registry=self.registry,
            transport=self.transport,
        )

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._start_time: float = 0.0
        self._last_poll_mono: float = 0.0
        self._poll_count: int = 0
        self._lock = threading.RLock()

        # Thread-safe telemetry cache
        self._latest_batches: Dict[str, NormalisedTelemetryBatch] = {}
        self._last_successful_reading_utc: Dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def uptime_seconds(self) -> float:
        if not self._running or self._start_time == 0.0:
            return 0.0
        return round(time.time() - self._start_time, 2)

    # -------------------------------------------------------------------------
    # Lifecycle Control
    # -------------------------------------------------------------------------

    def start(self) -> bool:
        """Start the monitoring engine and background polling loop."""
        with self._lock:
            if self._running:
                logger.warning("LocalAgentService already running.")
                return True

            logger.info("Starting LocalAgentService...")
            self._running = True
            self._start_time = time.time()
            self._last_poll_mono = time.monotonic()

            # Start engine
            self.engine.start()

            # Execute an initial scan immediately
            try:
                initial_batches = self.engine.poll_once()
                self._update_telemetry_cache(initial_batches)
            except Exception as exc:
                logger.error("Error during initial poll cycle: %s", exc)

            # Spawn dedicated polling daemon thread
            self._thread = threading.Thread(
                target=self._polling_loop,
                name="LocalAgent-PollWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info("LocalAgentService started successfully.")
            return True

    def stop(self) -> None:
        """Gracefully stop background polling and shutdown engine adapters."""
        with self._lock:
            if not self._running:
                return

            logger.info("Stopping LocalAgentService...")
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("LocalAgentService worker thread did not terminate within timeout.")

        with self._lock:
            try:
                self.engine.stop()
            except Exception as exc:
                logger.warning("Error stopping GatewayEngine: %s", exc)
            logger.info("LocalAgentService stopped cleanly.")

    def _polling_loop(self) -> None:
        """Background thread executing scheduled poll cycles with responsive interruption."""
        poll_interval = max(0.05, float(self.config.poll_interval_sec))

        while self._running:
            try:
                loop_start = time.monotonic()
                batches = self.engine.poll_once()
                self._update_telemetry_cache(batches)
                self._poll_count += 1
                self._last_poll_mono = time.monotonic()

                # Sleep remaining cycle time in fine slices for responsive stop()
                elapsed = time.monotonic() - loop_start
                sleep_sec = max(0.01, poll_interval - elapsed)
                sleep_end = time.monotonic() + sleep_sec

                while self._running and time.monotonic() < sleep_end:
                    step = min(0.05, max(0.005, sleep_end - time.monotonic()))
                    time.sleep(step)
            except Exception as exc:
                logger.error("Exception in LocalAgentService polling worker: %s", exc)
                time.sleep(0.5)

    def _update_telemetry_cache(self, batches: List[NormalisedTelemetryBatch]) -> None:
        """Update cached batch and track last successful readings."""
        now_utc = utc_now_iso()
        with self._lock:
            for b in batches:
                self._latest_batches[b.device_id] = b
                # If batch is connected and contains at least one GOOD reading, update timestamp
                has_good_data = any(r.quality == DataQuality.GOOD for r in b.readings.values())
                if b.connected or has_good_data:
                    self._last_successful_reading_utc[b.device_id] = b.timestamp_utc or now_utc

    # -------------------------------------------------------------------------
    # API Inspection Methods (Thread-Safe Reads from Cache)
    # -------------------------------------------------------------------------

    def get_health(self) -> Dict[str, Any]:
        """Return overall health status of agent and monitoring engine."""
        active_devs = self.engine.active_devices
        total_enabled = len([d for d in self.config.devices if d.enabled])
        connected_count = sum(1 for s in active_devs.values() if s.connected)

        if not self._running:
            agent_status = "stopped"
        elif total_enabled > 0 and connected_count == total_enabled:
            agent_status = "healthy"
        elif connected_count > 0:
            agent_status = "degraded"
        elif total_enabled == 0:
            agent_status = "healthy"
        else:
            agent_status = "unhealthy"

        return {
            "status": agent_status,
            "agent_version": "1.0.0",
            "uptime_seconds": self.uptime_seconds,
            "engine_running": self._running,
            "simulation_mode": self.simulation_mode,
            "poll_count": self._poll_count,
            "devices_total": len(self.config.devices),
            "devices_enabled": total_enabled,
            "devices_connected": connected_count,
            "gateway_id": self.config.id,
            "timestamp_utc": utc_now_iso(),
        }

    def get_devices(self) -> List[Dict[str, Any]]:
        """List all configured devices with connection status and last reading time."""
        active_devs = self.engine.active_devices
        result: List[Dict[str, Any]] = []

        for dev in self.config.devices:
            state: Optional[DeviceState] = active_devs.get(dev.id)
            connected = bool(state and state.connected)
            last_read = self._last_successful_reading_utc.get(dev.id)

            result.append({
                "id": dev.id,
                "name": dev.name,
                "manufacturer": dev.manufacturer,
                "model": dev.model,
                "adapter_id": dev.adapter_id,
                "enabled": dev.enabled,
                "connected": connected,
                "poll_interval_sec": dev.poll_interval_sec,
                "points_count": len(dev.points),
                "last_successful_reading_utc": last_read,
            })
        return result

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed definition and runtime state for a single device."""
        dev = next((d for d in self.config.devices if d.id == device_id), None)
        if not dev:
            return None

        state: Optional[DeviceState] = self.engine.active_devices.get(device_id)
        last_read = self._last_successful_reading_utc.get(device_id)

        points_summary = [
            {
                "point_id": p.point_id,
                "device_address": p.device_address,
                "data_type": p.data_type.value,
                "unit": p.engineering_units,
                "scale": p.scale_factor,
                "offset": p.offset,
                "description": p.description,
            }
            for p in dev.points
        ]

        return {
            "id": dev.id,
            "name": dev.name,
            "manufacturer": dev.manufacturer,
            "model": dev.model,
            "adapter_id": dev.adapter_id,
            "enabled": dev.enabled,
            "connected": bool(state and state.connected),
            "poll_interval_sec": dev.poll_interval_sec,
            "points": points_summary,
            "connection": redact_secrets(dev.connection),
            "last_successful_reading_utc": last_read,
            "total_polls": state.total_polls if state else 0,
            "consecutive_failures": state.consecutive_failures if state else 0,
        }

    def get_device_telemetry(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get latest normalized telemetry batch for a device.
        Returns None if device does not exist.
        Returns explicit unavailable status if no reading has been collected yet.
        """
        dev = next((d for d in self.config.devices if d.id == device_id), None)
        if not dev:
            return None

        with self._lock:
            batch = self._latest_batches.get(device_id)

        if not batch:
            return {
                "device_id": device_id,
                "status": "unavailable",
                "connected": False,
                "timestamp_utc": None,
                "scan_duration_ms": 0.0,
                "readings": {},
                "error": "No telemetry readings have been collected yet for this device.",
            }

        # Convert readings using normalized models enriched with point definitions
        serialized_readings = {}
        for pid, reading in batch.readings.items():
            r_dict = reading.to_dict()
            pt_def = dev.point_map.get(pid)
            if pt_def:
                r_dict["data_type"] = pt_def.data_type.value.lower()
                r_dict["engineering_units"] = pt_def.engineering_units
                r_dict["description"] = pt_def.description
            serialized_readings[pid] = r_dict

        return {
            "device_id": batch.device_id,
            "gateway_id": batch.gateway_id,
            "sequence_number": batch.sequence_number,
            "timestamp_utc": batch.timestamp_utc,
            "connected": batch.connected,
            "scan_duration_ms": batch.scan_duration_ms,
            "status": "online" if batch.connected else "offline",
            "error": batch.error,
            "readings": serialized_readings,
            "simulation_mode": self.simulation_mode,
        }

    def get_device_diagnostics(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get driver-level diagnostics for a device.
        Sanitizes connection parameters to prevent leaking passwords or credentials.
        """
        dev = next((d for d in self.config.devices if d.id == device_id), None)
        if not dev:
            return None

        state: Optional[DeviceState] = self.engine.active_devices.get(device_id)
        adapter_diag: Dict[str, Any] = {}
        device_info: Dict[str, Any] = {}

        if state and state.adapter:
            try:
                adapter_diag = redact_secrets(state.adapter.get_diagnostics())
                device_info = state.adapter.get_device_info()
            except Exception as exc:
                logger.warning("Failed querying diagnostics for '%s': %s", device_id, exc)

        last_error = state.last_batch.error if state and state.last_batch else None

        return {
            "device_id": device_id,
            "adapter_id": dev.adapter_id,
            "connected": bool(state and state.connected),
            "total_polls": state.total_polls if state else 0,
            "consecutive_failures": state.consecutive_failures if state else 0,
            "last_error": last_error,
            "adapter_diagnostics": adapter_diag,
            "device_hardware_info": device_info,
            "connection_summary": redact_secrets(dev.connection),
        }

    def get_agent_info(self) -> Dict[str, Any]:
        """Return agent metadata, registered adapter families, and capability flags."""
        supported_adapters = list(self.registry.list_adapters().keys())
        return {
            "agent_name": "Universal PLC Local Agent",
            "agent_version": "1.0.0",
            "api_version": "v1",
            "operating_mode": "simulation" if self.simulation_mode else "production",
            "is_strictly_read_only": True,
            "supported_adapters": supported_adapters,
            "capabilities": {
                "multi_device_polling": True,
                "read_only_enforcement": True,
                "simulation_mode": True,
                "modbus_tcp": True,
                "mitsubishi_mc_protocol": True,
                "opc_ua": False,  # Future phase
                "mqtt_export": False,  # Future phase
            },
        }

    def get_config_status(self) -> Dict[str, Any]:
        """Return non-sensitive configuration status and loaded device summary."""
        devices_summary = [
            {
                "id": d.id,
                "name": d.name,
                "adapter_id": d.adapter_id,
                "enabled": d.enabled,
                "points_count": len(d.points),
            }
            for d in self.config.devices
        ]

        return {
            "source_file": self.config.source_file or "in-memory",
            "is_valid": True,
            "gateway_id": self.config.id,
            "poll_interval_sec": self.config.poll_interval_sec,
            "heartbeat_sec": self.config.heartbeat_sec,
            "devices_count": len(self.config.devices),
            "devices": devices_summary,
        }
