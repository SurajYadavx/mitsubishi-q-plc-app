"""
agent/cli.py
────────────
Command-line interface entry point for the Universal PLC Windows Local Agent.

Usage:
  python -m agent.cli --simulate
  python -m agent.cli --host 127.0.0.1 --port 8000
  python -m agent.cli --config config/devices.yaml --api-key my-secret-key
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config_loader import load_system_config
from agent.api import create_app
from agent.credentials import (
    DEFAULT_API_KEY_TARGET,
    DEFAULT_MQTT_KEY_TARGET,
    delete_credential,
    has_credential,
    is_credential_manager_available,
    resolve_agent_api_key,
    set_credential,
)
from agent.security import SecurityPolicy, UnsafeBindingError, validate_host_binding
from agent.service import LocalAgentService

logger = logging.getLogger("agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Universal PLC Windows Local Agent Daemon (STRICTLY READ-ONLY)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind the API server (default is safe loopback: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port for Local Agent REST API",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to devices.yaml or legacy settings.ini (default: auto-detect)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode using virtual PLCs for offline development",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key for authenticating HTTP requests (can also be set via Windows Credential Manager or AGENT_API_KEY env var)",
    )
    parser.add_argument(
        "--store-key",
        action="store_true",
        help="Securely prompt for an API key and store it in Windows Credential Manager (UniversalPlc/AgentApiKey)",
    )
    parser.add_argument(
        "--delete-key",
        action="store_true",
        help="Remove stored API key from Windows Credential Manager",
    )
    parser.add_argument(
        "--credential-status",
        action="store_true",
        help="Check Windows Credential Manager availability and report stored secrets status (without displaying secrets)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Explicitly permit binding to non-loopback network interfaces (strictly requires API authentication)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Action 1: Credential Status Check
    if args.credential_status:
        avail = is_credential_manager_available()
        print("=== Universal PLC Credential Vault Status ===")
        print(f"Windows Credential Manager Available: {avail}")
        if avail:
            has_api = has_credential(DEFAULT_API_KEY_TARGET)
            has_mqtt = has_credential(DEFAULT_MQTT_KEY_TARGET)
            print(f"API Key ({DEFAULT_API_KEY_TARGET}): {'[STORED IN VAULT]' if has_api else '[NOT CONFIGURED]'}")
            print(f"MQTT Key ({DEFAULT_MQTT_KEY_TARGET}): {'[STORED IN VAULT]' if has_mqtt else '[NOT CONFIGURED]'}")
        else:
            print("Windows Credential Manager is unavailable on this platform.")
        sys.exit(0)

    # Action 2: Store Key into Windows Credential Manager
    if args.store_key:
        if not is_credential_manager_available():
            logger.critical("Windows Credential Manager is not available on this platform.")
            sys.exit(1)
        import getpass
        try:
            entered_key = getpass.getpass("Enter Agent API Key to store in Windows Credential Manager: ")
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled.")
            sys.exit(1)

        clean_key = entered_key.strip()
        if not clean_key:
            logger.error("Cannot store an empty API key.")
            sys.exit(1)

        ok = set_credential(DEFAULT_API_KEY_TARGET, clean_key)
        if ok:
            print(f"Successfully stored API key in Windows Credential Manager ('{DEFAULT_API_KEY_TARGET}').")
            sys.exit(0)
        else:
            logger.critical("Failed storing API key in Windows Credential Manager.")
            sys.exit(1)

    # Action 3: Delete Key from Windows Credential Manager
    if args.delete_key:
        if not is_credential_manager_available():
            logger.critical("Windows Credential Manager is not available on this platform.")
            sys.exit(1)
        ok = delete_credential(DEFAULT_API_KEY_TARGET)
        if ok:
            print(f"Successfully deleted API key from Windows Credential Manager ('{DEFAULT_API_KEY_TARGET}').")
            sys.exit(0)
        else:
            print(f"Credential '{DEFAULT_API_KEY_TARGET}' not found or could not be deleted.")
            sys.exit(1)

    # Resolve API key following strict security precedence (Vault -> Env Var -> CLI arg -> dev mode)
    api_key, auth_source = resolve_agent_api_key(cli_arg=args.api_key)
    has_auth = bool(api_key)

    # 1. Enforce local security policy on host binding
    try:
        validate_host_binding(args.host, allow_remote=args.allow_remote, has_auth=has_auth)
    except UnsafeBindingError as exc:
        logger.critical("Security violation on host binding: %s", exc)
        sys.exit(1)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "⚠️  WARNING: Binding Local Agent to non-loopback interface '%s'. "
            "Ensure firewall rules and API authentication are configured.",
            args.host,
        )

    # 2. Load system configuration
    config_path = Path(args.config) if args.config else None
    try:
        config = load_system_config(config_path)
        logger.info("Loaded configuration source: %s", config.source_file or "auto-detected")
    except Exception as exc:
        logger.critical("Failed loading configuration: %s", exc)
        sys.exit(1)

    # 3. Instantiate LocalAgentService and SecurityPolicy
    security_policy = SecurityPolicy(
        api_key=api_key,
        allow_remote=args.allow_remote,
    )
    service = LocalAgentService(
        config=config,
        simulation_mode=args.simulate,
    )

    # 4. Create FastAPI app
    app = create_app(service=service, security_policy=security_policy)

    # 5. Start background monitoring engine
    service.start()

    logger.info("=" * 65)
    logger.info("  Universal PLC Windows Local Agent API")
    logger.info("  Listening on http://%s:%d", args.host, args.port)
    logger.info("  Interactive OpenAPI Docs: http://%s:%d/docs", args.host, args.port)
    logger.info("  Mode: %s | Strictly Read-Only: True", "SIMULATION" if args.simulate else "PRODUCTION")
    logger.info("  Auth: %s", "API Key required" if api_key else "Loopback dev (unauthenticated)")
    logger.info("  Auth Source: %s", auth_source)
    logger.info("=" * 65)

    # 6. Run Uvicorn server
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received – shutting down Local Agent.")
    finally:
        service.stop()
        logger.info("Local Agent terminated cleanly.")


if __name__ == "__main__":
    main()
