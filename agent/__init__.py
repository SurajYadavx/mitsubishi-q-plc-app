"""
agent package
─────────────
Windows Local Agent for the Universal PLC Monitoring Platform.
Exposes secure, read-only REST APIs for PLC device telemetry, inventory,
and driver diagnostics.
"""

from agent.api import create_app
from agent.security import (
    SecurityPolicy,
    UnsafeBindingError,
    redact_secrets,
    validate_device_id,
    validate_host_binding,
)
from agent.service import LocalAgentService

__all__ = [
    "create_app",
    "LocalAgentService",
    "SecurityPolicy",
    "UnsafeBindingError",
    "validate_device_id",
    "validate_host_binding",
    "redact_secrets",
]
