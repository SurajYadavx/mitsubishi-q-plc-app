"""
gateway.py
──────────
Main entry point for the Windows PLC gateway.

Orchestrates:
  1. PLCReader  – reads Mitsubishi Q03UDECPU via MC Protocol/TCP
  2. SerialPublisher – sends JSON over USB serial to NodeMCU ESP8266

Run with:
  python gateway.py [--config path/to/settings.ini]

This process must be running on the Windows laptop whenever live PLC
monitoring is required.  It has NO write capability toward the PLC.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import sys
import time
from pathlib import Path

# Ensure src/ is importable when run from the gateway/ root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from plc_reader import PLCReader
from serial_publisher import SerialPublisher

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / "logs"
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
# Configuration loader
# ─────────────────────────────────────────────────────────────────────────────

def load_config(config_path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    read_files = cfg.read([config_path], encoding="utf-8")
    if not read_files:
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    # Optionally overlay secrets.ini (not committed to Git)
    secrets_path = Path(config_path).parent / "secrets.ini"
    if secrets_path.exists():
        cfg.read(str(secrets_path), encoding="utf-8")
        logger.info("Secrets overlay loaded from %s", secrets_path)

    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mitsubishi Q-PLC Gateway (READ-ONLY)")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "settings.ini"),
        help="Path to settings.ini",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    # ── PLC settings ──────────────────────────────────────────────────
    plc_ip           = cfg.get("plc", "ip")
    plc_port         = cfg.getint("plc", "port")
    plc_timeout      = cfg.getfloat("plc", "timeout_sec")
    plc_poll_sec     = cfg.getfloat("plc", "poll_interval_sec")
    plc_max_retry    = cfg.getint("plc", "max_retry")
    plc_retry_delay  = cfg.getfloat("plc", "retry_delay_sec")

    input_devices  = [d.strip() for d in cfg.get("devices", "inputs").split(",")]
    output_devices = [d.strip() for d in cfg.get("devices", "outputs").split(",")]

    # ── Serial settings ───────────────────────────────────────────────
    serial_port    = cfg.get("serial", "port")
    serial_baud    = cfg.getint("serial", "baud_rate")
    serial_timeout = cfg.getfloat("serial", "timeout_sec")

    # ── Gateway settings ──────────────────────────────────────────────
    gateway_id    = cfg.get("gateway", "id")
    heartbeat_sec = cfg.getfloat("gateway", "heartbeat_sec")

    logger.info("═══ Gateway starting  id=%s ═══", gateway_id)
    logger.info("PLC target: %s:%d  poll=%.1fs", plc_ip, plc_port, plc_poll_sec)
    logger.info("Serial: %s @ %d baud", serial_port, serial_baud)

    reader = PLCReader(
        ip=plc_ip,
        port=plc_port,
        timeout=plc_timeout,
        input_devices=input_devices,
        output_devices=output_devices,
    )
    publisher = SerialPublisher(
        port=serial_port,
        baud_rate=serial_baud,
        timeout=serial_timeout,
        gateway_id=gateway_id,
    )

    # ── Open serial first (ESP8266 must be connected) ─────────────────
    if not publisher.open():
        logger.warning("Serial port unavailable – will retry in loop")

    # ── Connect to PLC with bounded retries ───────────────────────────
    retry_count = 0
    while not reader.connect():
        retry_count += 1
        if retry_count >= plc_max_retry:
            logger.critical(
                "Could not connect to PLC after %d attempts – exiting.",
                plc_max_retry,
            )
            publisher.close()
            sys.exit(1)
        logger.warning("Retrying PLC connection (%d/%d) in %.1fs…",
                       retry_count, plc_max_retry, plc_retry_delay)
        time.sleep(plc_retry_delay)

    # ── Main poll loop ────────────────────────────────────────────────
    last_heartbeat_t = time.monotonic()
    plc_retry_count  = 0

    try:
        while True:
            loop_start = time.monotonic()

            # ── Re-open serial if it dropped ──────────────────────────
            if not publisher.is_open:
                logger.info("Attempting to reopen serial port…")
                publisher.open()

            # ── Poll PLC ──────────────────────────────────────────────
            reading = reader.poll()

            if reading.connected:
                plc_retry_count = 0
                publisher.publish_reading(reading)

            else:
                plc_retry_count += 1
                logger.warning(
                    "PLC read failed (attempt %d): %s", plc_retry_count, reading.error
                )
                publisher.publish_reading(reading)  # sends gateway_error type

                # Attempt reconnect after every poll failure
                reader.disconnect()
                time.sleep(plc_retry_delay)
                if not reader.connect():
                    logger.error("PLC reconnect failed")
                else:
                    logger.info("PLC reconnected after %d failures", plc_retry_count)

            # ── Heartbeat ─────────────────────────────────────────────
            now = time.monotonic()
            if now - last_heartbeat_t >= heartbeat_sec:
                publisher.publish_heartbeat(connected=reader.connected)
                last_heartbeat_t = now

            # ── Pace the loop ─────────────────────────────────────────
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.0, plc_poll_sec - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt – shutting down.")
    finally:
        reader.disconnect()
        publisher.publish_heartbeat(connected=False, error="Gateway shutdown")
        publisher.close()
        logger.info("═══ Gateway stopped ═══")


if __name__ == "__main__":
    main()
