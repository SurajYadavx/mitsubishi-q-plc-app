"""
agent/credentials.py
────────────────────
Windows Credential Manager integration for the Universal PLC Monitoring Platform.

Enables secure storage and retrieval of API keys, broker tokens, and PLC connection
passwords using the native Windows Credential Manager (Advapi32.dll) via standard ctypes.
Secrets are never written to source code, logged, or exposed in plain text files.
On non-Windows systems or when Credential Manager is unavailable, falls back gracefully.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Standard namespaced credential targets
DEFAULT_API_KEY_TARGET = "UniversalPlc/AgentApiKey"
DEFAULT_MQTT_KEY_TARGET = "UniversalPlc/MqttCredentials"

# Windows Credential Manager Constants
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
CRED_PERSIST_SESSION = 1

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _PCREDENTIAL = ctypes.POINTER(_CREDENTIAL)

    _CredRead = advapi32.CredReadW
    _CredRead.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCREDENTIAL)]
    _CredRead.restype = wintypes.BOOL

    _CredWrite = advapi32.CredWriteW
    _CredWrite.argtypes = [_PCREDENTIAL, wintypes.DWORD]
    _CredWrite.restype = wintypes.BOOL

    _CredDelete = advapi32.CredDeleteW
    _CredDelete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _CredDelete.restype = wintypes.BOOL

    _CredFree = advapi32.CredFree
    _CredFree.argtypes = [ctypes.c_void_p]
    _CredFree.restype = None


def is_credential_manager_available() -> bool:
    """Return True if running on Windows with Advapi32 Credential Manager accessible."""
    return _IS_WINDOWS


def get_credential(target_name: str) -> Optional[str]:
    """
    Retrieve a secret string from Windows Credential Manager.
    Returns None if the credential is not found, on non-Windows systems, or on error.
    """
    if not _IS_WINDOWS:
        return None

    try:
        pcred = _PCREDENTIAL()
        ok = _CredRead(target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if not ok:
            return None

        try:
            if not pcred.contents.CredentialBlob or pcred.contents.CredentialBlobSize == 0:
                return ""
            raw_bytes = ctypes.string_at(
                pcred.contents.CredentialBlob,
                pcred.contents.CredentialBlobSize,
            )
            # Attempt UTF-16-LE first (standard Windows wide-char encoding), then UTF-8 fallback
            try:
                return raw_bytes.decode("utf-16-le")
            except UnicodeDecodeError:
                return raw_bytes.decode("utf-8", errors="replace")
        finally:
            _CredFree(pcred)
    except Exception as exc:
        logger.debug("Failed reading credential '%s' from Windows Credential Manager: %s", target_name, exc)
        return None


def set_credential(
    target_name: str,
    secret: str,
    comment: str = "Universal PLC Agent Credential",
    username: str = "UniversalPlcAgent",
) -> bool:
    """
    Store or update a secret string in Windows Credential Manager.
    Uses CRED_PERSIST_LOCAL_MACHINE so secrets survive restarts.
    """
    if not _IS_WINDOWS:
        logger.warning("Windows Credential Manager is only available on Windows platforms.")
        return False

    try:
        encoded_secret = secret.encode("utf-16-le")
        cred = _CREDENTIAL()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target_name
        cred.Comment = comment
        cred.CredentialBlobSize = len(encoded_secret)
        cred.CredentialBlob = ctypes.cast(
            ctypes.c_char_p(encoded_secret),
            ctypes.POINTER(ctypes.c_byte),
        )
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = username

        ok = bool(_CredWrite(ctypes.byref(cred), 0))
        if not ok:
            err = ctypes.GetLastError()
            logger.error("CredWrite failed for '%s' with Win32 error code %d", target_name, err)
        return ok
    except Exception as exc:
        logger.error("Exception storing credential '%s': %s", target_name, exc)
        return False


def delete_credential(target_name: str) -> bool:
    """Delete a secret from Windows Credential Manager. Returns True on success."""
    if not _IS_WINDOWS:
        return False

    try:
        ok = bool(_CredDelete(target_name, CRED_TYPE_GENERIC, 0))
        return ok
    except Exception as exc:
        logger.debug("Exception deleting credential '%s': %s", target_name, exc)
        return False


def has_credential(target_name: str) -> bool:
    """Check if a credential target exists in Windows Credential Manager without exposing its content."""
    return get_credential(target_name) is not None


def get_device_password_target(device_id: str) -> str:
    """Generate standardized target name for a specific device password."""
    return f"UniversalPlc/PlcPassword/{device_id.strip()}"


def resolve_agent_api_key(
    cli_arg: Optional[str] = None,
    vault_target: str = DEFAULT_API_KEY_TARGET,
) -> Tuple[Optional[str], str]:
    """
    Resolve API key following strict security precedence:
    1. Windows Credential Manager (most secure: OS-backed vault, not exposed in process tables)
    2. Environment Variable AGENT_API_KEY or PLC_AGENT_API_KEY (secure for containers/services)
    3. Command-line argument --api-key (logged with security warning)
    4. None (unauthenticated local loopback)

    Returns tuple (api_key_value, source_description).
    """
    # 1. Windows Credential Manager
    if is_credential_manager_available():
        vault_key = get_credential(vault_target)
        if vault_key and vault_key.strip():
            return vault_key.strip(), f"Windows Credential Manager ('{vault_target}')"

    # 2. Environment Variables
    env_key = os.getenv("AGENT_API_KEY") or os.getenv("PLC_AGENT_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip(), "Environment Variable (AGENT_API_KEY)"

    # 3. CLI Argument (with security warning)
    if cli_arg and cli_arg.strip():
        logger.warning(
            "⚠️  SECURITY NOTICE: API key was provided via command-line argument (--api-key). "
            "Secrets passed via CLI arguments can be visible in OS process listings (Task Manager, Get-Process). "
            "Recommendation: Store the key in Windows Credential Manager via 'python -m agent.cli --store-key' "
            "or set the AGENT_API_KEY environment variable."
        )
        return cli_arg.strip(), "Command-Line Argument (--api-key) [PROCESS LISTING RISK]"

    # 4. No authentication configured
    return None, "Unauthenticated (Default Loopback Development Mode)"
