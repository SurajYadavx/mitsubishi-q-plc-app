"""
gateway.py
──────────
Main entry point for the Windows PLC Gateway Daemon.
Updated for Universal PLC Platform (Phase 02).

Orchestrates:
  1. Multi-device configuration loading (config/devices.yaml or fallback settings.ini)
  2. Protocol adapter resolution via AdapterRegistry
  3. Independent polling loops with isolated device failure handling
  4. Serial transport streaming to NodeMCU ESP8266 (COM9 @ 115200 baud)

Run with:
  python gateway.py [--config path/to/devices.yaml|settings.ini]

This process is strictly read-only and contains NO write commands toward the PLC.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Add repository root and gateway/src to sys.path
GATEWAY_DIR = Path(__file__).resolve().parent
REPO_ROOT = GATEWAY_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GATEWAY_DIR / "src") not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR / "src"))

from core.config_loader import load_system_config
from core.engine import GatewayEngine

# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR = GATEWAY_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "gateway.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("gateway")


# ─────────────────────────────────────────────────────────────────────────────
# Main Execution Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal PLC Gateway Daemon (STRICTLY READ-ONLY)"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to devices.yaml or legacy settings.ini (default: auto-detect)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Enable simulation mode for offline verification without physical hardware",
    )
    args = parser.parse_args()

    # Load validated configuration
    try:
        config = load_system_config(args.config)
        logger.info("Loaded configuration from: %s", config.source_file)
        logger.info("Gateway ID: %s | Poll interval: %.1fs | Heartbeat: %.1fs",
                    config.id, config.poll_interval_sec, config.heartbeat_sec)
        logger.info("Serial transport: %s @ %d baud", config.serial_port, config.serial_baud)

        # If --simulate flag is set, enable any simulated devices or adapt devices
        if args.simulate:
            for dev in config.devices:
                dev.adapter_id = "simulated"
                dev.enabled = True
            logger.info("Simulation mode ACTIVE: all devices mapped to simulated adapter")

        enabled_devs = config.get_enabled_devices()
        logger.info("Active enabled devices (%d):", len(enabled_devs))
        for dev in enabled_devs:
            logger.info("  • [%s] %s (adapter: %s, points: %d)",
                        dev.id, dev.name, dev.adapter_id, len(dev.points))

    except Exception as exc:
        logger.critical("Failed to load configuration: %s", exc)
        sys.exit(1)

    # Initialize and execute Gateway Engine
    engine = GatewayEngine(config=config)

    try:
        engine.run_loop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received – initiating graceful shutdown.")
    finally:
        engine.stop()
        logger.info("Gateway process terminated cleanly.")


if __name__ == "__main__":
    main()
