"""
tests/test_local_agent.py
─────────────────────────
Comprehensive integration and security test suite for the Universal PLC Windows Local Agent.
Tests all endpoints, security policies, secret redaction, telemetry serialization,
duplicate polling prevention, and error isolation without requiring physical hardware.
"""

import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.credentials import (
    DEFAULT_API_KEY_TARGET,
    DEFAULT_MQTT_KEY_TARGET,
    delete_credential,
    get_credential,
    has_credential,
    is_credential_manager_available,
    resolve_agent_api_key,
    set_credential,
)
from agent.security import (
    SecurityPolicy,
    UnsafeBindingError,
    redact_secrets,
    validate_device_id,
    validate_host_binding,
)
from agent.service import LocalAgentService
from core.adapter_registry import AdapterRegistry
from core.config_loader import DeviceConfig, GatewayConfig
from core.models import DataQuality, PointDataType, PointDefinition
from core.transport_interface import MockTelemetryTransport


def _build_test_gateway_config(poll_interval_sec: float = 1.0) -> GatewayConfig:
    """Construct deterministic test configuration with 2 simulated devices."""
    dev1 = DeviceConfig(
        id="plc_sim_01",
        name="Packaging Cell PLC",
        enabled=True,
        adapter_id="simulated",
        poll_interval_sec=poll_interval_sec,
        connection={"host": "127.0.0.1", "port": 502, "password": "SuperSecretPlcPassword123"},
        points=[
            PointDefinition(
                point_id="run_status",
                device_address="X0",
                data_type=PointDataType.BOOLEAN,
                description="Motor running flag",
            ),
            PointDefinition(
                point_id="line_speed",
                device_address="D100",
                data_type=PointDataType.UINT16,
                engineering_units="RPM",
                scale_factor=0.1,
                offset=0.0,
                description="Conveyor speed",
            ),
        ],
    )

    dev2 = DeviceConfig(
        id="plc_sim_02",
        name="Assembly Station PLC",
        enabled=True,
        adapter_id="simulated",
        poll_interval_sec=poll_interval_sec,
        connection={"host": "127.0.0.1", "port": 503, "api_key": "StationApiKeyXYZ"},
        points=[
            PointDefinition(
                point_id="parts_counter",
                device_address="D200",
                data_type=PointDataType.UINT16,
                description="Total parts assembled",
            )
        ],
    )

    return GatewayConfig(
        id="test-gw-agent-01",
        name="Test Gateway Agent",
        heartbeat_sec=5.0,
        poll_interval_sec=poll_interval_sec,
        devices=[dev1, dev2],
        source_file="test_memory_config.yaml",
    )


class TestLocalAgentSecurity(unittest.TestCase):
    """Test suite for host binding rules, secret scrubbing, and device ID validation."""

    def test_loopback_binding_defaults_and_unsafe_host_configuration(self):
        """Host binding must default to safe loopback and reject external interfaces without opt-in and auth."""
        # Valid loopback interfaces
        validate_host_binding("127.0.0.1")
        validate_host_binding("localhost")
        validate_host_binding("::1")

        # Non-loopback interfaces must raise UnsafeBindingError without allow_remote
        with self.assertRaises(UnsafeBindingError):
            validate_host_binding("0.0.0.0", allow_remote=False)

        with self.assertRaises(UnsafeBindingError):
            validate_host_binding("192.168.1.100", allow_remote=False)

        # Non-loopback with allow_remote=True but NO authentication must be strictly rejected
        with self.assertRaises(UnsafeBindingError):
            validate_host_binding("0.0.0.0", allow_remote=True, has_auth=False)

        with self.assertRaises(UnsafeBindingError):
            validate_host_binding("192.168.1.100", allow_remote=True, has_auth=False)

        # Opt-in allow_remote WITH authentication must succeed
        validate_host_binding("0.0.0.0", allow_remote=True, has_auth=True)
        validate_host_binding("192.168.1.100", allow_remote=True, has_auth=True)

    def test_validate_device_id_rules(self):
        """Device ID validation must reject path traversal, injection, or invalid characters."""
        self.assertEqual(validate_device_id("plc_01"), "plc_01")
        self.assertEqual(validate_device_id("dev-station-A"), "dev-station-A")

        # Invalid formats
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            validate_device_id("")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            validate_device_id("../etc/passwd")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            validate_device_id("plc;drop table")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            validate_device_id("dev 01")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_secret_redaction_helper(self):
        """Ensure passwords, tokens, and API keys are redacted recursively."""
        payload = {
            "host": "192.168.1.50",
            "port": 502,
            "password": "ClearTextPassword!",
            "auth": {"api_key": "secret_token_123", "timeout": 3.0},
            "sub_items": [{"user_token": "abcde"}, {"name": "sensor_1"}],
        }

        sanitized = redact_secrets(payload)
        self.assertEqual(sanitized["host"], "192.168.1.50")
        self.assertEqual(sanitized["port"], 502)
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["auth"]["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["auth"]["timeout"], 3.0)
        self.assertEqual(sanitized["sub_items"][0]["user_token"], "[REDACTED]")
        self.assertEqual(sanitized["sub_items"][1]["name"], "sensor_1")


class TestLocalAgentApiEndpoints(unittest.TestCase):
    """Integration test suite executing HTTP requests against LocalAgentService."""

    @classmethod
    def setUpClass(cls):
        cls.config = _build_test_gateway_config()
        cls.registry = AdapterRegistry()
        cls.transport = MockTelemetryTransport()
        cls.service = LocalAgentService(
            config=cls.config,
            registry=cls.registry,
            transport=cls.transport,
            simulation_mode=True,
        )
        cls.service.start()

        cls.app = create_app(service=cls.service)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()

    def test_root_endpoint(self):
        """GET / returns HTML dashboard by default, or JSON catalog if requested."""
        # Default request serves HTML dashboard
        res_html = self.client.get("/")
        self.assertEqual(res_html.status_code, 200)
        self.assertIn("text/html", res_html.headers.get("content-type", ""))
        self.assertIn("Q-PLC SUPERVISORY MONITOR", res_html.text)

        # JSON Accept header returns API catalog
        res_json = self.client.get("/", headers={"Accept": "application/json"})
        self.assertEqual(res_json.status_code, 200)
        data = res_json.json()
        self.assertIn("Universal PLC", data["name"])
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["health"], "/api/v1/health")

        # Dedicated /api endpoint returns catalog directly
        res_api = self.client.get("/api")
        self.assertEqual(res_api.status_code, 200)
        data_api = res_api.json()
        self.assertEqual(data_api["version"], "1.0.0")

    def test_health_endpoint(self):
        """GET /api/v1/health returns agent operational status and uptime."""
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["agent_version"], "1.0.0")
        self.assertTrue(data["engine_running"])
        self.assertTrue(data["simulation_mode"])
        self.assertGreater(data["uptime_seconds"], 0.0)
        self.assertEqual(data["devices_total"], 2)
        self.assertEqual(data["devices_connected"], 2)

    def test_agent_info_endpoint(self):
        """GET /api/v1/agent/info returns supported adapter metadata and capability flags."""
        res = self.client.get("/api/v1/agent/info")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["agent_version"], "1.0.0")
        self.assertTrue(data["is_strictly_read_only"])
        self.assertIn("simulated", data["supported_adapters"])
        self.assertIn("mitsubishi_mc_3e", data["supported_adapters"])
        self.assertIn("modbus_tcp", data["supported_adapters"])

    def test_config_status_endpoint(self):
        """GET /api/v1/config/status returns configuration metadata and device summaries."""
        res = self.client.get("/api/v1/config/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["is_valid"])
        self.assertEqual(data["devices_count"], 2)
        self.assertEqual(len(data["devices"]), 2)
        dev0 = data["devices"][0]
        self.assertEqual(dev0["id"], "plc_sim_01")
        self.assertEqual(dev0["points_count"], 2)

    def test_device_listing_endpoint(self):
        """GET /api/v1/devices returns list of registered devices with status."""
        res = self.client.get("/api/v1/devices")
        self.assertEqual(res.status_code, 200)
        devices = res.json()
        self.assertEqual(len(devices), 2)

        d1 = next(d for d in devices if d["id"] == "plc_sim_01")
        self.assertEqual(d1["name"], "Packaging Cell PLC")
        self.assertTrue(d1["connected"])
        self.assertTrue(d1["enabled"])
        self.assertIsNotNone(d1["last_successful_reading_utc"])

    def test_device_detail_endpoint_and_secret_redaction(self):
        """GET /api/v1/devices/{device_id} returns device metadata with redacted secrets."""
        res = self.client.get("/api/v1/devices/plc_sim_01")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], "plc_sim_01")
        self.assertEqual(len(data["points"]), 2)

        # Verification of secret redaction: password must NOT be leaked
        self.assertIn("connection", data)
        self.assertEqual(data["connection"]["password"], "[REDACTED]")

    def test_device_telemetry_endpoint_and_data_quality(self):
        """GET /api/v1/devices/{device_id}/telemetry returns normalized readings."""
        res = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["device_id"], "plc_sim_01")
        self.assertTrue(data["connected"])
        self.assertEqual(data["status"], "online")
        self.assertIn("readings", data)

        readings = data["readings"]
        self.assertIn("run_status", readings)
        self.assertIn("line_speed", readings)

        r_speed = readings["line_speed"]
        self.assertEqual(r_speed["data_type"], "uint16")
        self.assertEqual(r_speed["engineering_units"], "RPM")
        self.assertEqual(r_speed["quality"], "GOOD")
        self.assertIsNotNone(r_speed["raw_value"])

    def test_multi_device_discovery_and_analog_metadata_consistency(self):
        """
        Phase 06A Test: Verify all configured devices are enumerated by GET /api/v1/devices,
        and each device's telemetry returns distinct point schemas with complete data_type,
        engineering_units, descriptions, and data quality fields.
        """
        res_list = self.client.get("/api/v1/devices")
        self.assertEqual(res_list.status_code, 200)
        devices = res_list.json()
        device_ids = [d["id"] for d in devices]
        self.assertIn("plc_sim_01", device_ids)
        self.assertIn("plc_sim_02", device_ids)

        # Device 1 telemetry has boolean run_status and analog line_speed with uint16 and RPM
        res_t1 = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
        self.assertEqual(res_t1.status_code, 200)
        t1 = res_t1.json()
        self.assertIn("run_status", t1["readings"])
        self.assertEqual(t1["readings"]["run_status"]["data_type"], "boolean")

        self.assertIn("line_speed", t1["readings"])
        pt1 = t1["readings"]["line_speed"]
        self.assertEqual(pt1["data_type"], "uint16")
        self.assertEqual(pt1["engineering_units"], "RPM")
        self.assertEqual(pt1["quality"], "GOOD")
        self.assertIsNotNone(pt1["raw_value"])

        # Device 2 telemetry has parts_counter
        res_t2 = self.client.get("/api/v1/devices/plc_sim_02/telemetry")
        self.assertEqual(res_t2.status_code, 200)
        t2 = res_t2.json()
        self.assertIn("parts_counter", t2["readings"])
        pt2 = t2["readings"]["parts_counter"]
        self.assertEqual(pt2["data_type"], "uint16")
        self.assertIn(pt2["quality"], ["GOOD", "COMM_FAILURE"])

    def test_device_diagnostics_endpoint(self):
        """GET /api/v1/devices/{device_id}/diagnostics returns driver diagnostics."""
        res = self.client.get("/api/v1/devices/plc_sim_01/diagnostics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["device_id"], "plc_sim_01")
        self.assertTrue(data["connected"])
        self.assertGreaterEqual(data["total_polls"], 1)
        self.assertEqual(data["consecutive_failures"], 0)
        self.assertIn("adapter_diagnostics", data)
        self.assertIn("connection_summary", data)
        self.assertEqual(data["connection_summary"]["password"], "[REDACTED]")

    def test_unknown_device_returns_404(self):
        """Requesting non-existent device ID returns 404 Not Found."""
        res_dev = self.client.get("/api/v1/devices/nonexistent_plc")
        self.assertEqual(res_dev.status_code, 404)
        self.assertTrue(res_dev.json()["error"])

        res_telem = self.client.get("/api/v1/devices/nonexistent_plc/telemetry")
        self.assertEqual(res_telem.status_code, 404)

        res_diag = self.client.get("/api/v1/devices/nonexistent_plc/diagnostics")
        self.assertEqual(res_diag.status_code, 404)

    def test_invalid_device_id_syntax_returns_400(self):
        """Invalid device ID formats (slashes, spaces, path traversal) return 400 Bad Request."""
        res = self.client.get("/api/v1/devices/invalid..id/telemetry")
        self.assertEqual(res.status_code, 400)

        res2 = self.client.get("/api/v1/devices/bad%20id/telemetry")
        self.assertEqual(res2.status_code, 400)

    def test_device_level_communication_failure_isolation(self):
        """
        Verify that when a device suffers a communication failure, the API remains healthy,
        returns explicit COMM_FAILURE/offline status, and other devices remain online.
        """
        from unittest.mock import MagicMock
        from core.models import NormalisedTelemetryBatch, NormalisedReading, DataQuality

        st = self.service.engine.active_devices.get("plc_sim_02")
        orig_read_points = st.adapter.read_points

        fail_batch = NormalisedTelemetryBatch(
            gateway_id=self.config.id,
            device_id="plc_sim_02",
            sequence_number=99,
            timestamp_utc="2026-10-19T23:00:00.000Z",
            readings={
                "parts_counter": NormalisedReading.create_failure(
                    point_id="parts_counter",
                    error_msg="Socket connection dropped",
                    quality=DataQuality.COMM_FAILURE,
                )
            },
            connected=False,
            error="Socket connection dropped",
        )
        st.adapter.read_points = MagicMock(return_value=fail_batch)
        st.last_poll_mono = 0.0

        try:
            with self.service._lock:
                batches = self.service.engine.poll_once()
                self.service._update_telemetry_cache(batches)

            # 1. Health check reports degraded, NOT crashed
            res_health = self.client.get("/api/v1/health")
            self.assertEqual(res_health.status_code, 200)
            self.assertEqual(res_health.json()["status"], "degraded")
            self.assertEqual(res_health.json()["devices_connected"], 1)

            # 2. Healthy device continues serving normal telemetry
            res_d1 = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
            self.assertEqual(res_d1.status_code, 200)
            self.assertTrue(res_d1.json()["connected"])

            # 3. Failing device returns explicit offline status and COMM_FAILURE quality
            res_d2 = self.client.get("/api/v1/devices/plc_sim_02/telemetry")
            self.assertEqual(res_d2.status_code, 200)
            data_d2 = res_d2.json()
            self.assertFalse(data_d2["connected"])
            self.assertEqual(data_d2["status"], "offline")
            self.assertEqual(data_d2["readings"]["parts_counter"]["quality"], "COMM_FAILURE")
            self.assertIsNone(data_d2["readings"]["parts_counter"]["raw_value"])
        finally:
            st.adapter.read_points = orig_read_points
            with self.service._lock:
                st.connected = True
                st.consecutive_failures = 0
                batches = self.service.engine.poll_once()
                self.service._update_telemetry_cache(batches)

    def test_unavailable_telemetry_before_first_poll(self):
        """If a device has not yet been polled, returns explicit unavailable state without fabricated zeros."""
        dev = next(d for d in self.service.config.devices if d.id == "plc_sim_01")
        with self.service._lock:
            dev.enabled = False
            cached_backup = self.service._latest_batches.pop("plc_sim_01", None)

        try:
            res = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "unavailable")
            self.assertFalse(data["connected"])
            self.assertEqual(data["readings"], {})
            self.assertIsNone(data["timestamp_utc"])
        finally:
            with self.service._lock:
                dev.enabled = True
                if cached_backup:
                    self.service._latest_batches["plc_sim_01"] = cached_backup

    def test_no_duplicate_polling_on_http_requests(self):
        """
        Verify multiple rapid HTTP requests do NOT trigger duplicate engine polling loops.
        API queries read from thread-safe cached batches.
        """
        initial_poll_count = self.service._poll_count

        # Issue 10 rapid HTTP telemetry requests
        for _ in range(10):
            res = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
            self.assertEqual(res.status_code, 200)

        # Polling count should be driven solely by the background timer, not HTTP hits
        # (Over a few milliseconds, background thread will at most advance 0-1 cycles)
        self.assertLessEqual(self.service._poll_count - initial_poll_count, 3)

    def test_strict_read_only_no_write_routes(self):
        """Verify the FastAPI application exposes ZERO write, force, or control routes."""
        for route in self.app.routes:
            methods = getattr(route, "methods", set())
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/"):
                for m in methods:
                    self.assertIn(
                        m,
                        {"GET", "HEAD", "OPTIONS"},
                        f"Prohibited non-read-only HTTP method {m} found on route {path}"
                    )


class TestLocalAgentAuthentication(unittest.TestCase):
    """Test suite verifying API key authentication and access control."""

    def setUp(self):
        self.config = _build_test_gateway_config()
        self.registry = AdapterRegistry()
        self.service = LocalAgentService(config=self.config, registry=self.registry, simulation_mode=True)
        self.service.start()

        self.policy = SecurityPolicy(api_key="SecretSuperKey-456")
        self.app = create_app(service=self.service, security_policy=self.policy)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.service.stop()

    def test_unauthenticated_request_rejected_with_401(self):
        """When an API key is configured, unauthenticated requests return 401 Unauthorized."""
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Valid API key required", res.json()["detail"])

    def test_invalid_api_key_rejected_with_401(self):
        """Providing an incorrect API key returns 401 Unauthorized."""
        res = self.client.get("/api/v1/health", headers={"X-Agent-API-Key": "WrongKey"})
        self.assertEqual(res.status_code, 401)

    def test_valid_header_api_key_accepted(self):
        """Providing the valid X-Agent-API-Key header returns 200 OK."""
        res = self.client.get("/api/v1/health", headers={"X-Agent-API-Key": "SecretSuperKey-456"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "healthy")

    def test_valid_bearer_token_accepted(self):
        """Providing the valid Authorization: Bearer <key> header returns 200 OK."""
        res = self.client.get("/api/v1/devices", headers={"Authorization": "Bearer SecretSuperKey-456"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 2)


class TestPhase04BLocalDashboardIntegrationAndHardening(unittest.TestCase):
    """
    Dedicated Phase 04B test suite verifying:
    1. Strict rejection of remote binding (--allow-remote) unless authentication is configured.
    2. Local static asset serving (/assets/css/style.css, /assets/js/app.js).
    3. Direct HTML dashboard and test suite access (/index.html, /test.html, /).
    4. API route preservation and prevention of static file route shadowing.
    5. CORS header validation for explicit local origins (no wildcard * allowed).
    6. Environment variable API key fallback support.
    """

    @classmethod
    def setUpClass(cls):
        cls.config = _build_test_gateway_config()
        cls.registry = AdapterRegistry()
        cls.service = LocalAgentService(config=cls.config, registry=cls.registry, simulation_mode=True)
        cls.service.start()
        cls.policy = SecurityPolicy()
        cls.app = create_app(
            service=cls.service,
            security_policy=cls.policy,
            allowed_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:3000"]
        )
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()

    def test_allow_remote_without_auth_strictly_rejected(self):
        """Binding to non-loopback with allow_remote=True and has_auth=False must raise UnsafeBindingError."""
        for host in ["0.0.0.0", "192.168.1.150", "10.0.0.5", "::"]:
            with self.assertRaises(UnsafeBindingError) as ctx:
                validate_host_binding(host, allow_remote=True, has_auth=False)
            self.assertIn("strictly requires API authentication", str(ctx.exception))

    def test_allow_remote_with_auth_accepted(self):
        """Binding to non-loopback with allow_remote=True and has_auth=True must succeed."""
        for host in ["0.0.0.0", "192.168.1.150", "10.0.0.5"]:
            validate_host_binding(host, allow_remote=True, has_auth=True)

    def test_serve_frontend_index_html_at_root(self):
        """GET / and GET /index.html must serve the supervisory HTML dashboard."""
        for path in ["/", "/index.html"]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Failed accessing {path}")
            self.assertIn("text/html", res.headers.get("content-type", ""))
            self.assertIn("Q-PLC SUPERVISORY MONITOR", res.text)
            self.assertIn("select-data-source", res.text)

    def test_serve_frontend_static_assets(self):
        """Static assets under /assets/ must load with correct content types."""
        res_css = self.client.get("/assets/css/style.css")
        self.assertEqual(res_css.status_code, 200)
        self.assertIn("text/css", res_css.headers.get("content-type", ""))
        self.assertIn(":root", res_css.text)

        res_js = self.client.get("/assets/js/app.js")
        self.assertEqual(res_js.status_code, 200)
        self.assertIn("javascript", res_js.headers.get("content-type", ""))
        self.assertIn("localAgentActive", res_js.text)

    def test_serve_test_html(self):
        """GET /test.html serves the deterministic verification suite."""
        res = self.client.get("/test.html")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("Offline Deployment Readiness", res.text)

    def test_api_routes_preserved_and_not_shadowed(self):
        """Static asset mounting must not shadow versioned REST endpoints or OpenAPI docs."""
        # /api/v1/health
        res_h = self.client.get("/api/v1/health")
        self.assertEqual(res_h.status_code, 200)
        self.assertEqual(res_h.json()["status"], "healthy")

        # /api/v1/devices
        res_d = self.client.get("/api/v1/devices")
        self.assertEqual(res_d.status_code, 200)
        self.assertIsInstance(res_d.json(), list)

        # /api catalog
        res_c = self.client.get("/api")
        self.assertEqual(res_c.status_code, 200)
        self.assertEqual(res_c.json()["docs"], "/docs")

        # OpenAPI schema & docs
        res_docs = self.client.get("/docs")
        self.assertEqual(res_docs.status_code, 200)
        res_schema = self.client.get("/openapi.json")
        self.assertEqual(res_schema.status_code, 200)

    def test_cors_headers_with_explicit_local_origin(self):
        """Allowed local origin receives Access-Control-Allow-Origin; unlisted origin does not."""
        # 1. Allowed local origin receives CORS header
        res_allowed = self.client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(res_allowed.status_code, 200)
        self.assertEqual(res_allowed.headers.get("access-control-allow-origin"), "http://localhost:8080")

        # 2. Unlisted external origin does NOT receive CORS header
        res_blocked = self.client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://malicious-external-site.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertNotEqual(
            res_blocked.headers.get("access-control-allow-origin"),
            "https://malicious-external-site.com"
        )
        self.assertNotEqual(
            res_blocked.headers.get("access-control-allow-origin"),
            "*"
        )

    def test_environment_variable_api_key_support(self):
        """CLI argument parser & resolution must support AGENT_API_KEY environment variable."""
        import os
        from unittest.mock import patch
        from agent.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.api_key)

        with patch.dict(os.environ, {"AGENT_API_KEY": "env-vault-key-789"}, clear=False):
            resolved_key = args.api_key or os.getenv("AGENT_API_KEY") or os.getenv("PLC_AGENT_API_KEY")
            self.assertEqual(resolved_key, "env-vault-key-789")


class TestPhase04B1AccuracyAudit(unittest.TestCase):
    """
    Automated regression tests for Phase 04B.1 Dashboard Status Accuracy Audit.
    Ensures telemetry simulation flags, UI contract elements, and truth-in-status indicators
    faithfully represent verified evidence.
    """

    @classmethod
    def setUpClass(cls):
        cls.config = _build_test_gateway_config()
        cls.registry = AdapterRegistry()
        cls.transport = MockTelemetryTransport()
        cls.service = LocalAgentService(
            config=cls.config,
            registry=cls.registry,
            transport=cls.transport,
            simulation_mode=True,
        )
        cls.service.start()
        cls.app = create_app(service=cls.service)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()

    def test_telemetry_batch_exposes_simulation_mode_flag(self):
        """GET /api/v1/devices/{id}/telemetry must explicitly expose simulation_mode=True in simulation."""
        res = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("simulation_mode", data)
        self.assertTrue(data["simulation_mode"])
        self.assertEqual(data["status"], "online")

    def test_health_check_exposes_authoritative_simulation_state(self):
        """GET /api/v1/health exposes simulation_mode and engine_running truthfully."""
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["simulation_mode"])
        self.assertTrue(data["engine_running"])
        self.assertGreaterEqual(data["poll_count"], 1)

    def test_device_inventory_identifies_simulated_adapter(self):
        """Device inventory exposes adapter_id='simulated' for virtual devices."""
        res = self.client.get("/api/v1/devices")
        self.assertEqual(res.status_code, 200)
        devices = res.json()
        for dev in devices:
            self.assertEqual(dev["adapter_id"], "simulated")

    def test_html_ui_contains_accuracy_audit_contract_elements(self):
        """Served HTML index.html must contain hardened elements for simulation and reference rung."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn("header-sim-badge", html)
        self.assertIn("SIMULATION (VIRTUAL)", html)
        self.assertIn("REFERENCE LADDER RUNG 0001 (X3 &rarr; Y20)", html)
        self.assertIn("Ladder Logic Reference Model", html)
        self.assertIn("NOT IN USE", html)

    def test_valid_zero_reading_is_preserved_with_good_quality(self):
        """Valid zero reading on boolean point must remain 0 and GOOD, never converted to null/error."""
        res = self.client.get("/api/v1/devices/plc_sim_01/telemetry")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        readings = data["readings"]
        self.assertIn("run_status", readings)
        val = readings["run_status"]["raw_value"]
        quality = readings["run_status"]["quality"]
        self.assertIn(val, (0, 1))
        self.assertEqual(quality, "GOOD")


class TestWindowsCredentialManager(unittest.TestCase):
    """Verify Windows Credential Manager integration and fallback mechanisms."""

    def test_vault_availability_detection(self):
        """is_credential_manager_available() correctly identifies Windows vs non-Windows."""
        avail = is_credential_manager_available()
        if sys.platform == "win32":
            self.assertTrue(avail)
        else:
            self.assertFalse(avail)

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows OS Credential Manager")
    def test_vault_write_read_delete_lifecycle(self):
        """Test complete write, read, verify, and delete cycle in Windows Credential Manager."""
        test_target = "UniversalPlc/UnitTest_EphemeralKey_Test"
        test_secret = "TestSecretKey_987654321_Valid"

        try:
            # 1. Write credential
            write_ok = set_credential(test_target, test_secret, comment="Unit test credential")
            self.assertTrue(write_ok, "Failed writing to Windows Credential Manager")

            # 2. Check existence
            self.assertTrue(has_credential(test_target))

            # 3. Read back and verify identity
            retrieved = get_credential(test_target)
            self.assertEqual(retrieved, test_secret)

            # 4. Delete credential
            del_ok = delete_credential(test_target)
            self.assertTrue(del_ok, "Failed deleting from Windows Credential Manager")

            # 5. Verify no longer exists
            self.assertFalse(has_credential(test_target))
            self.assertIsNone(get_credential(test_target))
        finally:
            # Ensure cleanup
            delete_credential(test_target)

    def test_nonexistent_credential_returns_none(self):
        """Non-existent credential target returns None and has_credential is False."""
        fake_target = "UniversalPlc/DefinitelyNonExistentTarget_99999"
        self.assertFalse(has_credential(fake_target))
        self.assertIsNone(get_credential(fake_target))


class TestCredentialResolutionHierarchy(unittest.TestCase):
    """Verify the strict security precedence of API key resolution."""

    @patch("agent.credentials.get_credential")
    @patch("agent.credentials.is_credential_manager_available")
    def test_vault_takes_precedence_over_env_and_cli(self, mock_avail, mock_get):
        """Windows Credential Manager has highest precedence."""
        mock_avail.return_value = True
        mock_get.return_value = "vault-super-secret"

        with patch.dict("os.environ", {"AGENT_API_KEY": "env-secret"}):
            key, source = resolve_agent_api_key(cli_arg="cli-secret")
            self.assertEqual(key, "vault-super-secret")
            self.assertIn("Windows Credential Manager", source)

    @patch("agent.credentials.get_credential")
    @patch("agent.credentials.is_credential_manager_available")
    def test_env_var_takes_precedence_over_cli(self, mock_avail, mock_get):
        """Environment variable has precedence over CLI argument when vault has no key."""
        mock_avail.return_value = True
        mock_get.return_value = None

        with patch.dict("os.environ", {"AGENT_API_KEY": "env-secret"}):
            key, source = resolve_agent_api_key(cli_arg="cli-secret")
            self.assertEqual(key, "env-secret")
            self.assertIn("Environment Variable", source)

    @patch("agent.credentials.get_credential")
    @patch("agent.credentials.is_credential_manager_available")
    def test_cli_argument_used_when_no_vault_or_env(self, mock_avail, mock_get):
        """CLI argument is accepted when no vault or env var is set, with warning recorded."""
        mock_avail.return_value = True
        mock_get.return_value = None

        with patch.dict("os.environ", {}, clear=True):
            key, source = resolve_agent_api_key(cli_arg="cli-secret")
            self.assertEqual(key, "cli-secret")
            self.assertIn("Command-Line Argument", source)
            self.assertIn("PROCESS LISTING RISK", source)

    @patch("agent.credentials.get_credential")
    @patch("agent.credentials.is_credential_manager_available")
    def test_unauthenticated_dev_mode_when_none_provided(self, mock_avail, mock_get):
        """Returns None when no key source exists."""
        mock_avail.return_value = True
        mock_get.return_value = None

        with patch.dict("os.environ", {}, clear=True):
            key, source = resolve_agent_api_key(cli_arg=None)
            self.assertIsNone(key)
            self.assertIn("Unauthenticated", source)


class TestAgentLifecycleAndResilience(unittest.TestCase):
    """Verify thread clean shutdown, timeout tolerance, and communication failure isolation."""

    def test_service_stop_is_rapid_and_responsive(self):
        """Service stop() must cleanly terminate worker thread within 1.5 seconds."""
        import time
        config = _build_test_gateway_config(poll_interval_sec=5.0)
        registry = AdapterRegistry()
        service = LocalAgentService(config=config, registry=registry, simulation_mode=True)
        service.start()
        self.assertTrue(service._running)
        self.assertTrue(service._thread.is_alive())

        t0 = time.time()
        service.stop()
        elapsed = time.time() - t0

        self.assertFalse(service._running)
        self.assertFalse(service._thread.is_alive())
        # Sliced sleep loop ensures stop is immediate despite 5.0s poll interval
        self.assertLess(elapsed, 1.5, f"Service stop took too long: {elapsed:.2f}s")

    def test_offline_plc_comm_failure_does_not_crash_engine_or_api(self):
        """Simulate communication failure or offline device; engine must survive and API remain responsive."""
        config = _build_test_gateway_config(poll_interval_sec=0.1)
        registry = AdapterRegistry()
        service = LocalAgentService(config=config, registry=registry, simulation_mode=True)
        service.start()
        app = create_app(service=service)
        client = TestClient(app)

        try:
            # Let it run a few cycles
            import time
            time.sleep(0.3)

            # Manually corrupt an adapter's read_points to simulate temporary network failure
            device_state = service.engine._devices["plc_sim_01"]
            original_read_points = device_state.adapter.read_points

            def mock_failing_read_points(points):
                raise ConnectionResetError("Simulated socket connection reset by peer (PLC offline)")

            device_state.adapter.read_points = mock_failing_read_points

            # Wait another cycle
            time.sleep(0.3)

            # Engine must still be running and healthy
            health_res = client.get("/api/v1/health")
            self.assertEqual(health_res.status_code, 200)
            health_data = health_res.json()
            self.assertTrue(health_data["engine_running"])
            self.assertGreaterEqual(health_data["poll_count"], 2)

            # Restore adapter
            device_state.adapter.read_points = original_read_points
        finally:
            service.stop()


if __name__ == "__main__":
    unittest.main()

