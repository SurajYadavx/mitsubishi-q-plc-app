"""
core/config_loader.py
─────────────────────
Multi-Device Configuration Loader and Validator.
Loads config/devices.yaml with full validation and provides backward-compatible
fallback to legacy settings.ini files.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.adapter_registry import AdapterRegistry, default_adapter_registry
from core.models import PointDataType, PointDefinition

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


@dataclass
class DeviceConfig:
    """Validated configuration for an individual controller/device."""
    id: str
    name: str
    enabled: bool
    adapter_id: str
    manufacturer: str = "Unknown"
    model: str = "Generic"
    connection: Dict[str, Any] = field(default_factory=dict)
    poll_interval_sec: float = 1.0
    points: List[PointDefinition] = field(default_factory=list)

    @property
    def point_map(self) -> Dict[str, PointDefinition]:
        """Dictionary of point_id -> PointDefinition for O(1) lookups."""
        return {p.point_id: p for p in self.points}


@dataclass
class GatewayConfig:
    """Validated configuration for the host gateway runtime."""
    id: str
    name: str = "Universal PLC Gateway"
    heartbeat_sec: float = 5.0
    poll_interval_sec: float = 1.0
    serial_port: str = "COM9"
    serial_baud: int = 115200
    serial_timeout: float = 1.0
    devices: List[DeviceConfig] = field(default_factory=list)
    source_file: str = ""

    def get_enabled_devices(self) -> List[DeviceConfig]:
        """Return only active devices configured for polling."""
        return [d for d in self.devices if d.enabled]


def _validate_device_address(adapter_id: str, address: str) -> None:
    """Verify that a device address is syntactically plausible for the specified adapter."""
    if not address or not isinstance(address, str):
        raise ConfigValidationError("Device address must be a non-empty string")
    
    addr = address.strip()
    if len(addr) < 2:
        raise ConfigValidationError(f"Address '{address}' is too short to be valid")

    if adapter_id == "mitsubishi_mc_3e":
        dev_type = addr[0].upper()
        if dev_type not in ("X", "Y", "M", "D", "B", "W", "R", "L", "F", "V", "S"):
            raise ConfigValidationError(
                f"Unsupported Mitsubishi device type prefix '{dev_type}' in address '{address}'"
            )
        base = 16 if dev_type in ("X", "Y", "B", "W") else 10
        try:
            int(addr[1:], base)
        except ValueError:
            raise ConfigValidationError(
                f"Invalid numeric address '{addr[1:]}' in Mitsubishi address '{address}' (expected base {base})"
            )

    elif adapter_id == "modbus_tcp":
        from adapters.modbus.modbus_tcp import parse_modbus_address
        try:
            parse_modbus_address(addr)
        except ValueError as exc:
            raise ConfigValidationError(f"Invalid Modbus address '{address}': {exc}") from exc


def load_yaml_config(
    yaml_path: Path,
    registry: Optional[AdapterRegistry] = None,
) -> GatewayConfig:
    """
    Load and thoroughly validate a devices.yaml configuration file.
    Raises ConfigValidationError if any constraint is violated.
    """
    if not yaml_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

    registry = registry or default_adapter_registry

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise ConfigValidationError(f"YAML parsing error in {yaml_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigValidationError(f"Root of {yaml_path} must be a mapping/dict")

    # 1. Validate gateway section
    gw_sec = data.get("gateway", {})
    if not isinstance(gw_sec, dict):
        raise ConfigValidationError("Section 'gateway' must be a dictionary")
    
    gw_id = gw_sec.get("id")
    if not gw_id or not isinstance(gw_id, str):
        raise ConfigValidationError("Missing or invalid 'gateway.id' string")

    gw_name = str(gw_sec.get("name", "Universal PLC Gateway"))
    heartbeat_sec = float(gw_sec.get("heartbeat_sec", 5.0))
    poll_interval_sec = float(gw_sec.get("poll_interval_sec", 1.0))
    serial_sec = data.get("serial", {})
    serial_port = str(serial_sec.get("port", "COM9"))
    serial_baud = int(serial_sec.get("baud_rate", 115200))
    serial_timeout = float(serial_sec.get("timeout_sec", 1.0))

    # 2. Validate devices section
    raw_devices = data.get("devices", [])
    if not isinstance(raw_devices, list):
        raise ConfigValidationError("Section 'devices' must be a list of device configurations")

    devices: List[DeviceConfig] = []
    seen_device_ids: set[str] = set()

    for idx, dev_raw in enumerate(raw_devices):
        if not isinstance(dev_raw, dict):
            raise ConfigValidationError(f"Device at index {idx} must be a dictionary")

        dev_id = dev_raw.get("id")
        if not dev_id or not isinstance(dev_id, str):
            raise ConfigValidationError(f"Device at index {idx} has missing or empty 'id'")

        if dev_id in seen_device_ids:
            raise ConfigValidationError(f"Duplicate device ID detected: '{dev_id}'")
        seen_device_ids.add(dev_id)

        name = str(dev_raw.get("name", dev_id))
        enabled = bool(dev_raw.get("enabled", True))
        adapter_id = dev_raw.get("adapter_id")
        if not adapter_id or not isinstance(adapter_id, str):
            raise ConfigValidationError(f"Device '{dev_id}' missing 'adapter_id'")

        # Verify adapter is known
        if not registry.is_registered(adapter_id):
            known = ", ".join(registry.list_adapters().keys())
            raise ConfigValidationError(
                f"Device '{dev_id}' specifies unknown adapter '{adapter_id}'. Known adapters: [{known}]"
            )

        manufacturer = str(dev_raw.get("manufacturer", "Unknown"))
        model = str(dev_raw.get("model", "Generic"))
        conn = dev_raw.get("connection", {})
        if not isinstance(conn, dict):
            raise ConfigValidationError(f"Device '{dev_id}' connection section must be a dictionary")

        dev_poll = float(dev_raw.get("poll_interval_sec", poll_interval_sec))

        # Validate points
        raw_points = dev_raw.get("points", [])
        if not isinstance(raw_points, list):
            raise ConfigValidationError(f"Device '{dev_id}' points section must be a list")

        points: List[PointDefinition] = []
        seen_point_ids: set[str] = set()

        for p_idx, p_raw in enumerate(raw_points):
            if not isinstance(p_raw, dict):
                raise ConfigValidationError(f"Device '{dev_id}' point at index {p_idx} must be a dictionary")

            p_id = p_raw.get("point_id")
            if not p_id or not isinstance(p_id, str):
                raise ConfigValidationError(f"Device '{dev_id}' point at index {p_idx} missing 'point_id'")

            if p_id in seen_point_ids:
                raise ConfigValidationError(f"Device '{dev_id}' contains duplicate point_id '{p_id}'")
            seen_point_ids.add(p_id)

            address = p_raw.get("device_address")
            if not address or not isinstance(address, str):
                raise ConfigValidationError(f"Device '{dev_id}' point '{p_id}' missing 'device_address'")

            _validate_device_address(adapter_id, address)

            data_type = PointDataType(p_raw.get("data_type", "BOOLEAN"))
            units = str(p_raw.get("engineering_units", ""))
            scale = float(p_raw.get("scale_factor", 1.0))
            offset = float(p_raw.get("offset", 0.0))
            desc = str(p_raw.get("description", ""))

            pt = PointDefinition(
                point_id=p_id,
                device_address=address,
                data_type=data_type,
                engineering_units=units,
                scale_factor=scale,
                offset=offset,
                description=desc,
            )
            points.append(pt)

        dev_cfg = DeviceConfig(
            id=dev_id,
            name=name,
            enabled=enabled,
            adapter_id=adapter_id,
            manufacturer=manufacturer,
            model=model,
            connection=conn,
            poll_interval_sec=dev_poll,
            points=points,
        )
        devices.append(dev_cfg)

    return GatewayConfig(
        id=gw_id,
        name=gw_name,
        heartbeat_sec=heartbeat_sec,
        poll_interval_sec=poll_interval_sec,
        serial_port=serial_port,
        serial_baud=serial_baud,
        serial_timeout=serial_timeout,
        devices=devices,
        source_file=str(yaml_path),
    )


def load_legacy_ini_config(ini_path: Path) -> GatewayConfig:
    """
    Load legacy gateway/config/settings.ini and convert it to normalized GatewayConfig.
    Ensures seamless backward compatibility.
    """
    if not ini_path.exists():
        raise FileNotFoundError(f"Legacy configuration file not found: {ini_path}")

    cfg = configparser.ConfigParser()
    cfg.read(str(ini_path), encoding="utf-8")

    # Optionally overlay secrets.ini
    secrets_path = ini_path.parent / "secrets.ini"
    if secrets_path.exists():
        cfg.read(str(secrets_path), encoding="utf-8")

    plc_ip = cfg.get("plc", "ip", fallback="192.168.3.39")
    plc_port = cfg.getint("plc", "port", fallback=5010)
    plc_timeout = cfg.getfloat("plc", "timeout_sec", fallback=3.0)
    poll_sec = cfg.getfloat("plc", "poll_interval_sec", fallback=1.0)
    frame_type = cfg.get("plc", "protocol", fallback="3E")

    inputs_str = cfg.get("devices", "inputs", fallback="X0,X1,X2,X3,X4,X5,X6,X7")
    outputs_str = cfg.get("devices", "outputs", fallback="Y20,Y21,Y22,Y23,Y24,Y25,Y26,Y27")

    serial_port = cfg.get("serial", "port", fallback="COM9")
    serial_baud = cfg.getint("serial", "baud_rate", fallback=115200)
    serial_timeout = cfg.getfloat("serial", "timeout_sec", fallback=1.0)

    gateway_id = cfg.get("gateway", "id", fallback="plc-gw-01")
    heartbeat_sec = cfg.getfloat("gateway", "heartbeat_sec", fallback=5.0)

    # Build points list
    points: List[PointDefinition] = []
    for d in [item.strip() for item in inputs_str.split(",") if item.strip()]:
        points.append(PointDefinition(point_id=d, device_address=d, data_type=PointDataType.BOOLEAN))
    for d in [item.strip() for item in outputs_str.split(",") if item.strip()]:
        points.append(PointDefinition(point_id=d, device_address=d, data_type=PointDataType.BOOLEAN))

    device = DeviceConfig(
        id="melsec_q03",
        name="Mitsubishi Q03UDECPU (Legacy Config)",
        enabled=True,
        adapter_id="mitsubishi_mc_3e",
        manufacturer="Mitsubishi Electric",
        model="Q03UDECPU",
        connection={
            "host": plc_ip,
            "port": plc_port,
            "timeout_sec": plc_timeout,
            "frame_type": frame_type,
        },
        poll_interval_sec=poll_sec,
        points=points,
    )

    return GatewayConfig(
        id=gateway_id,
        name="Legacy Migrated Gateway",
        heartbeat_sec=heartbeat_sec,
        poll_interval_sec=poll_sec,
        serial_port=serial_port,
        serial_baud=serial_baud,
        serial_timeout=serial_timeout,
        devices=[device],
        source_file=str(ini_path),
    )


def load_system_config(
    custom_path: Optional[str] = None,
    registry: Optional[AdapterRegistry] = None,
) -> GatewayConfig:
    """
    Auto-detect and load configuration with priority:
      1. Explicit path passed via CLI (--config)
      2. config/devices.yaml (Modern multi-device registry)
      3. gateway/config/settings.ini (Legacy fallback)
    """
    repo_root = Path(__file__).resolve().parent.parent

    if custom_path:
        p = Path(custom_path)
        if not p.is_absolute():
            p = repo_root / p
        if p.suffix.lower() in (".yaml", ".yml"):
            return load_yaml_config(p, registry)
        return load_legacy_ini_config(p)

    # Priority 2: config/devices.yaml
    default_yaml = repo_root / "config" / "devices.yaml"
    if default_yaml.exists():
        try:
            return load_yaml_config(default_yaml, registry)
        except Exception as exc:
            logger.warning("Failed loading default devices.yaml (%s) – attempting legacy fallback", exc)

    # Priority 3: gateway/config/settings.ini
    default_ini = repo_root / "gateway" / "config" / "settings.ini"
    if default_ini.exists():
        logger.info("Loading legacy configuration from %s", default_ini)
        return load_legacy_ini_config(default_ini)

    raise FileNotFoundError("No valid configuration found in config/devices.yaml or gateway/config/settings.ini")
