"""
agent/security.py
─────────────────
Security controls, secret redaction, host binding policies, and authentication
for the Windows Local Agent.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Allowed loopback host strings
LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}

# Regex for device IDs: alphanumeric, underscores, hyphens only (1-64 chars)
DEVICE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Sensitive key names to redact from diagnostic/config endpoints
SENSITIVE_KEY_NAMES: Set[str] = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "credentials",
    "auth",
    "access_token",
}


class UnsafeBindingError(ValueError):
    """Raised when the agent attempts to bind to a non-loopback address without explicit opt-in."""
    pass


def validate_host_binding(host: str, allow_remote: bool = False, has_auth: bool = False) -> None:
    """
    Validate host binding and authentication requirements.
    By default, only loopback (127.0.0.1, localhost, ::1) is permitted.
    Non-loopback binding requires allow_remote=True AND has_auth=True.
    Specifying allow_remote without an API key configured is strictly rejected.
    """
    clean_host = host.strip().lower()

    # Reject remote exposure if no API key / authentication is configured
    if allow_remote and not has_auth:
        raise UnsafeBindingError(
            "Security violation: Remote network binding (--allow-remote) strictly requires API authentication. "
            "Configure an API key via Windows Credential Manager (--store-key), the AGENT_API_KEY environment "
            "variable, or --api-key before enabling remote access."
        )

    # Reject non-loopback binding without explicit allow_remote
    if clean_host not in LOOPBACK_HOSTS and not allow_remote:
        raise UnsafeBindingError(
            f"Unsafe network binding to '{host}'. By default, the Local Agent only binds to "
            "loopback (127.0.0.1). To explicitly expose the agent on external interfaces, "
            "provide --allow-remote with appropriate firewall and security controls."
        )


def validate_device_id(device_id: str) -> str:
    """
    Validate that device_id contains only safe alphanumeric, underscore, or hyphen characters.
    Prevents path traversal, SQL injection, or command injection via device identifiers.
    """
    if not device_id or not isinstance(device_id, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device ID must be a non-empty string.",
        )
    clean_id = device_id.strip()
    if not DEVICE_ID_REGEX.match(clean_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid device ID '{device_id}'. IDs must be 1-64 characters (alphanumeric, hyphens, underscores).",
        )
    return clean_id


def redact_secrets(data: Any) -> Any:
    """
    Recursively sanitize dictionaries and lists, redacting any sensitive field values.
    Ensures passwords, tokens, and private credentials are never exposed via API endpoints.
    """
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower().strip()
            if isinstance(v, (dict, list)):
                sanitized[k] = redact_secrets(v)
            elif any(s in k_lower for s in SENSITIVE_KEY_NAMES):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = v
        return sanitized
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


# Optional HTTP Bearer authentication scheme
bearer_scheme = HTTPBearer(auto_error=False)


class SecurityPolicy:
    """
    Local Agent Security Policy.
    Controls API key authentication and remote access permissions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        allow_remote: bool = False,
    ) -> None:
        self.api_key = api_key.strip() if api_key else None
        self.allow_remote = allow_remote

    def verify_auth(
        self,
        x_api_key: Optional[str] = Header(None, alias="X-Agent-API-Key"),
        bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    ) -> None:
        """
        FastAPI dependency enforcing API Key authentication when configured.
        Supports 'X-Agent-API-Key' header or 'Authorization: Bearer <key>'.
        If no api_key is configured on the agent, local loopback requests pass.
        """
        if not self.api_key:
            return  # No authentication required in default loopback dev mode

        provided_key: Optional[str] = None
        if x_api_key:
            provided_key = x_api_key.strip()
        elif bearer and bearer.credentials:
            provided_key = bearer.credentials.strip()

        if not provided_key or provided_key != self.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Valid API key required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
