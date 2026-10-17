"""
test_plc_connection.py
──────────────────────
Standalone diagnostic script – verifies the PLC TCP connection and
performs a few read-only polls WITHOUT the full gateway stack.

Run BEFORE starting the gateway to confirm:
  1. Python + pymcprotocol version
  2. PLC reachable at configured IP:port
  3. X0–X7 bits readable
  4. Y20–Y27 bits readable

Usage (in the gateway .venv):
  python test_plc_connection.py
  python test_plc_connection.py --ip 192.168.3.39 --port 5010 --polls 5
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pymcprotocol
from plc_reader import PLCReader

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("test")


def main() -> None:
    parser = argparse.ArgumentParser(description="PLC connection smoke-test (read-only)")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "settings.ini"),
        help="Path to settings.ini to load default PLC IP/port",
    )
    parser.add_argument("--ip", default=None, help="Override PLC IP (default: from settings.ini)")
    parser.add_argument("--port", type=int, default=None, help="Override PLC port (default: from settings.ini)")
    parser.add_argument("--polls", type=int, default=3)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    # Determine PLC IP and port: CLI override > config file > hardcoded fallback
    plc_ip = "192.168.3.39"
    plc_port = 5010

    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(str(cfg_path), encoding="utf-8")
        secrets_path = cfg_path.parent / "secrets.ini"
        if secrets_path.exists():
            cfg.read(str(secrets_path), encoding="utf-8")

        if cfg.has_section("plc"):
            plc_ip = cfg.get("plc", "ip", fallback=plc_ip)
            plc_port = cfg.getint("plc", "port", fallback=plc_port)

    if args.ip:
        plc_ip = args.ip
    if args.port:
        plc_port = args.port

    # ── Library version check ─────────────────────────────────────────
    logger.info("Python      : %s", sys.version)
    logger.info("pymcprotocol: %s", pymcprotocol.__version__)

    # ── Connection test ───────────────────────────────────────────────
    reader = PLCReader(ip=plc_ip, port=plc_port, timeout=3.0)

    if not reader.connect():
        logger.critical("FAIL - Could not connect to PLC at %s:%d", plc_ip, plc_port)
        sys.exit(1)

    logger.info("PASS - PLC TCP connection established")

    # ── Read-only polls ───────────────────────────────────────────────
    all_ok = True
    for i in range(1, args.polls + 1):
        reading = reader.poll()
        print(f"\n--- Poll {i}/{args.polls} --------------------------------")
        print(json.dumps(reading.__dict__, indent=2))

        if not reading.connected:
            logger.error("Poll %d FAILED: %s", i, reading.error)
            all_ok = False
        else:
            logger.info("Poll %d OK", i)

        if i < args.polls:
            time.sleep(args.interval)

    reader.disconnect()

    if all_ok:
        print("\n[PASS] All polls succeeded - gateway is ready to run.")
        sys.exit(0)
    else:
        print("\n[FAIL] One or more polls failed - review logs above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
