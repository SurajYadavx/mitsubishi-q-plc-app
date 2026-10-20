"""
tests/test_firmware_tls.py
──────────────────────────
Hardware-free offline security tests for Phase 08A:
ESP8266 Firmware MQTT/TLS Security Remediation.

Verifies:
1. Complete removal of wifiClient.setInsecure() from the firmware code.
2. Implementation of BearSSL::X509List trust anchors via setTrustAnchors() with global lifetime.
3. Fail-closed security logic: MQTT connection is aborted if Root CA is missing or placeholder.
4. Non-blocking NTP clock synchronization gating before TLS handshake.
5. Preservation of read-only industrial monitoring invariants.
6. Certificate template hygiene (ca_cert.h.example) and .gitignore protection.
7. Software-simulated TLS certificate verification using Python ssl module.

NOTE ON HARDWARE LIMITATION:
These tests validate static source code contracts, template hygiene, and simulated
TLS handshake behavior in software. They do NOT substitute for on-device flashing
and BearSSL runtime validation on physical ESP8266 hardware, which remains pending.
"""

from __future__ import annotations

import re
import socket
import ssl
import sys
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = REPO_ROOT / "firmware" / "plc_monitor"
INO_PATH = FIRMWARE_DIR / "plc_monitor.ino"
CA_EXAMPLE_PATH = FIRMWARE_DIR / "ca_cert.h.example"
SECRETS_EXAMPLE_PATH = FIRMWARE_DIR / "secrets.h.example"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


# ─────────────────────────────────────────────────────────────────────────────
# Test Certificate Fixtures (Generated for Offline Simulation)
# ─────────────────────────────────────────────────────────────────────────────

TEST_ROOT_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDMzCCAhugAwIBAgIUD1Ec9itTmAm/0juycvhl3Qko4EcwDQYJKoZIhvcNAQEL
BQAwITEfMB0GA1UEAwwWVW5pdmVyc2FsUGxjVGVzdFJvb3RDQTAeFw0yNjEwMjAw
NjMzMjdaFw0zNjEwMTcwNjMzMjdaMCExHzAdBgNVBAMMFlVuaXZlcnNhbFBsY1Rl
c3RSb290Q0EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDAtbEwRGSk
VGdqlCYISUc1Zr/jTQajIJY7f2qDXykVzbDI1XbjomIgU2Nagvo5YaudwUASKFpl
kAz9ZthNBdX6nyZcj0FXSZspd5ZtN6WWB/sj+ft4y6g3ZcYP92632p99JE3QBmR5
knVm1GTopvSqI2fdAHDkQjfPrMu5MzXCygdIgBJC0rZph22ujm1jZRXyRmu29auf
qxc4BRj8A8TJ37QcuyiDdKD3QK+4XG8nByLSrM+uqY1PzpPj06WJeIFDQBDaXGMP
8aJXQRvEvjTMO0mX0MjO7b56rjOHs0/a/TuJ/M3RFMDxQKc/HGDGlBcPkQ1Zek9v
xir+fIJRdv4ZAgMBAAGjYzBhMB0GA1UdDgQWBBSysanW5wZw4NBR8B/plDbbPPcj
MjAfBgNVHSMEGDAWgBSysanW5wZw4NBR8B/plDbbPPcjMjAPBgNVHRMBAf8EBTAD
AQH/MA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAQEAkbMqurYR8a2Q
kyG8dpQHb7ulDoR0mhosaUB+jL4gtbEu7sbVVVev/Zh97Kb+5fBqoIr+YcDaU0V5
JQSFLN2oTcg1EIetaI2H4CUyrnfflOJAb3QIzvUrW0e/Fxvj5mig3oYw40wymaIU
UZHSt/QcGlaVCY+YLIilhzhKWnckBNB74HrEyVFBCDaxAxtmHd9vz1zuwQdbtNP+
E1DI5osRQyvv3eiNgVrK13b0WOf5Vc/U2sJV3wn49J5ngVdbq2WONN0QD8Uup1Co
I7hEk6Y5MDh2+h97Ha9PC37HmnMnW1SR+gXhVVNKomal5CHfK17dPe2eNlCO2ezI
UZtUeU71qw==
-----END CERTIFICATE-----"""

TEST_SERVER_CRT_PEM = """-----BEGIN CERTIFICATE-----
MIIDVjCCAj6gAwIBAgIUJSariCZhfmm04x6m2nNjnR2fRKswDQYJKoZIhvcNAQEL
BQAwITEfMB0GA1UEAwwWVW5pdmVyc2FsUGxjVGVzdFJvb3RDQTAeFw0yNjEwMjAw
NjMzMjdaFw0zNjEwMTcwNjMzMjdaMBQxEjAQBgNVBAMMCWxvY2FsaG9zdDCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAIzIalf8FiZ+PlqbJMYXf5dSOJ1k
spTlSqkHE0HUwKpbWDzyICbjlcT4ZjhrNZJsW8B3nRHfTm/0CneP/uneY3u+cnlg
PnsEuwdz6v+1APV7gswSp2rtNTVTzFEAiSYllnskMfOdUApzJOOqivulxKhXa4s9
7oOYSLpw9bGd87wKUzKBqlCSw6rU4sbDTNlGAbYIRdoiZVoSk8fwK44aiY872kXu
p24846gmRlVatRJLgLhHRy1w43QyTd8HG1/3tjLjOaS+pnzNkZO7UT+YiU5nOs6N
FxZCHL5QVE6iIKIPz3HCz1bDdXRGPihHKouESsOgrPFsmpfmhwaKSSuCTWsCAwEA
AaOBkjCBjzAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIFoDATBgNVHSUEDDAK
BggrBgEFBQcDATAaBgNVHREEEzARgglsb2NhbGhvc3SHBH8AAAEwHQYDVR0OBBYE
FOBJ6C4z2LZfy60byou/WtmOgyhXMB8GA1UdIwQYMBaAFLKxqdbnBnDg0FHwH+mU
Nts89yMyMA0GCSqGSIb3DQEBCwUAA4IBAQCQkJtoLbJRFaN7ZD4A48hmJZDqpXEa
oGYfrgS09ybvYLPKTLypTHUKOUbLOhqtLiW2pVsj5j1VnfDg3IqPOBqUA2kh9C2I
yz+1jZKVm8SDbpuYO+xYXTEuVI+u2NhicBJDBAvvKNdhoKmSv4Z+a7BwvwZ4ldDw
BBIED+IB8SLxks8ZSn5lYSbJxeEKZsgYsbCI25VqkxAKg5dj6YPKQJUc1DSNi8hN
OmO7kBF8pYOibrG/TOs/PiOcC8JZ+bW2Q2Rv0ssrAXFdqGkbxtLapJgv85MPvTJv
fxQAsW1OuOsJnY8l/C2L67eTmB/C8n2TQemdRszLMQt9s7t1gscVVeIU
-----END CERTIFICATE-----"""

TEST_SERVER_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCMyGpX/BYmfj5a
myTGF3+XUjidZLKU5UqpBxNB1MCqW1g88iAm45XE+GY4azWSbFvAd50R305v9Ap3
j/7p3mN7vnJ5YD57BLsHc+r/tQD1e4LMEqdq7TU1U8xRAIkmJZZ7JDHznVAKcyTj
qor7pcSoV2uLPe6DmEi6cPWxnfO8ClMygapQksOq1OLGw0zZRgG2CEXaImVaEpPH
8CuOGomPO9pF7qduPOOoJkZVWrUSS4C4R0ctcON0Mk3fBxtf97Yy4zmkvqZ8zZGT
u1E/mIlOZzrOjRcWQhy+UFROoiCiD89xws9Ww3V0Rj4oRyqLhErDoKzxbJqX5ocG
ikkrgk1rAgMBAAECggEABkXj3yz4+7KCXfbHXU0TJVxhI21z1TvTGm1met3baK8O
fOC5Dm9aCN9pbQTx641VshTIwR23JZ8bj31+EuoS4SLfGb0J663BSH3uY07ezqsE
0zA9mRkd8G6RQXnwCOzoPW882Cl5TvRK5eeye6+rCWk9ZIRYI8r3hD85fQMxwH4p
910CY22OrKjkiMeBtp45wyBisJKRlTRmGuBB9P7MjJA6HJOkw7JRgcHpzHIsKadj
gpGc9XKDDlarXtFg8kI5QVtYQQ3kXqtqx/4m77IvDxXB+TFuaWS3VXpo62pIzWaJ
pG/P/QkNSJjJRnTcftrg9NmE+kaucQtvhreSLDT3aQKBgQC+UmKBWJ7ORHXF7ZIj
cT+mnlX5W4SqPwlrOkCI+ecrmLt9ccruGaCoSCCOE2LZybn1w3Wn3Uukn7waKtOm
HGmHqM5ruIQ1nS3PRihIAu+YH82aF7rLim38mLEfXbd43qECk1s+P2AnReONEk46
7LfhLEsA8Iyfr67OyoKyDbGsbQKBgQC9XZudI6ZrIf7bVrCi6r0xMVoWsHmZZFNU
w+bKiDY/UYR+a/HQAYgNFMlJhcVg0BXwIC9Ox0s7ANgqxZ2kLdWnbnL9rtCTA4Pt
+hr52zNjEhcE3gHlm16FML4nH3LwKt2R/rsOkjiMpaya0Kt6rqaOv/y08hbiHzHL
ybkju8IKNwKBgFmw75UvU1+3qZ0GUZl8VFfMRI4IECQ/Iswynpku1D+acbkzeEPv
NeQ4YYczIaGrTITCgV8DjhNd58KZLjcZKq5w4h6nzT6K/TyyPmd5YrpZOoaE6lXj
veONfeNfQginOk/Ba/2Q1lqLuCoSV2WzixCF1C70LgFPRN1VWHRaucGdAoGAU0qL
GdEGb/Th/mPhCaeqU6rbb0NsGNYR+SS5rLfA9JTMbDbag51I1EgiSR53tJ+SJq5h
YIs0Qd2bNyT4s7AdW6mvFnn/78oKC3CI0QFjVt4sFc34k5+FMZxA50Bj89NG5eJf
MIeMLADrLKU/55ROWleDtasN+aOy6Wnu7xK0xH0CgYBO65T4y5dyzNl8rw6nse4e
v0Pkn4OOyokCjMH88PMy32Jef3d20eKemVmgSk7WHw2bcJ7mBnxz3m1GREUF4XVR
OjhOIfxZyFQd/XRYGxk/mA+i61DKE1T+Iiyt2u3uHThejXqqc9g/WBZJxd07MRjb
heuFCr8OxKLCLaP6a4pXoQ==
-----END PRIVATE KEY-----"""

ROGUE_ROOT_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDLzCCAhegAwIBAgIUOtkFds7btAZ/MWZvh8CGoyBWRZkwDQYJKoZIhvcNAQEL
BQAwHzEdMBsGA1UEAwwUUm9ndWVVbnRydXN0ZWRSb290Q0EwHhcNMjYxMDIwMDYz
MzI3WhcNMzYxMDE3MDYzMzI3WjAfMR0wGwYDVQQDDBRSb2d1ZVVudHJ1c3RlZFJv
b3RDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAMs54EIbLY1Yj93y
6eI0noizr/QBa8tL0+yO/vJhYZGe5cV6FZjRX/+xsvtGvmfY8I5esSnYN5L6ytZk
C4L+eAKT/bgYiFsdKL/9Vo9defds+BPgE+e834dEQEopALmeBO/pFUs4S7BZvfiB
BLc8/pOtgnsEBi9j4IXZ1zcUZum9nNWAvtnjwpRlyYnEchOPIV0XJD1IGQAcs/Iq
m2URHx0eLXxglVlXw41gCeYv/YydBqGn/FBf5YQRN42xs7d4x+4V7S2vih7aYbGH
hMp867PHISMA8qd1mo8yNCwcpMoNkK2gZp2D4pG96KVob+oMmoBWdGm/uzkap4TN
tYTzFz8CAwEAAaNjMGEwHQYDVR0OBBYEFC6D5X9iRmXxxT1g8HhPV2zQolkcMB8G
A1UdIwQYMBaAFC6D5X9iRmXxxT1g8HhPV2zQolkcMA8GA1UdEwEB/wQFMAMBAf8w
DgYDVR0PAQH/BAQDAgEGMA0GCSqGSIb3DQEBCwUAA4IBAQDK2ZRQ8ZNfH0kDPvYN
d6hRg9w12GCq09phjNq8dIpq24YYf+cEBGI8v8hvbOURh+jfIHQ9QIq8ZW4dGcfQ
C7rImFpjw5RYIHQ50LrOkS/jx9C7KmEHldc3IIm+TGL/BPVo2X5oyRA575vTh4x8
HVIB2rSelLkAX8wpnVcizC+dYV7VC0d0wCgUn9Yl47w4A7xLU7Bv97OrfP62b8SF
D9HN0I2yY3Tdve2buE/N6OQt4e6jI7Q5MBh4/jRwD3xBf7WRF79Wmb+beWEnmr/d
ekDA1k8QaifyQGFa52DS53CeYy9eZP4ZfAi1W5zEN8eUmB/sh1QSzZ51V/F2wkbj
HHJ9
-----END CERTIFICATE-----"""


# ─────────────────────────────────────────────────────────────────────────────
# Test Classes
# ─────────────────────────────────────────────────────────────────────────────

class TestFirmwareTlsStatic(unittest.TestCase):
    """
    Static code analysis tests verifying that insecure TLS has been eradicated
    from plc_monitor.ino and replaced with fail-closed BearSSL trust anchors.
    """

    def setUp(self):
        self.assertTrue(INO_PATH.exists(), f"Missing firmware file: {INO_PATH}")
        self.source = INO_PATH.read_text(encoding="utf-8")

    def test_insecure_tls_completely_removed_from_code(self):
        """wifiClient.setInsecure() must NOT be called in any code statement."""
        # Find any non-comment line calling setInsecure
        code_lines = [
            line.strip() for line in self.source.splitlines()
            if not line.strip().startswith("//") and not line.strip().startswith("*")
        ]
        insecure_calls = [line for line in code_lines if "setInsecure" in line]
        self.assertEqual(
            insecure_calls,
            [],
            f"Prohibited setInsecure() method call found in firmware code: {insecure_calls}"
        )

    def test_trust_anchors_exact_api_and_global_lifetime(self):
        """
        BearSSL WiFiClientSecure must use setTrustAnchors(&trustedCA) (plural form).
        trustedCA must be declared with global/static lifetime to prevent dangling pointers.
        """
        # Exact API method check (plural setTrustAnchors)
        self.assertIn("setTrustAnchors", self.source)
        self.assertNotIn("setTrustAnchor(", self.source, "Misspelled singular 'setTrustAnchor(' used!")

        # Global lifetime check
        self.assertRegex(
            self.source,
            r"static\s+BearSSL::X509List\s+trustedCA\s*;",
            "trustedCA must be declared with global/static lifetime"
        )
        self.assertIn("wifiClient.setTrustAnchors(&trustedCA);", self.source)

    def test_fail_closed_logic_present(self):
        """
        Firmware must fail closed: if CA cert is missing, malformed, or placeholder,
        MQTT connection must be strictly aborted without insecure fallback.
        """
        self.assertIn("bool isCACertConfigured()", self.source)
        self.assertIn("PASTE_ROOT_CA_CERTIFICATE_HERE", self.source)
        self.assertIn("caConfiguredAndValid", self.source)
        self.assertIn("if (!caConfiguredAndValid)", self.source)
        self.assertIn("BLOCKED: Cannot connect to MQTT broker without valid Root CA", self.source)

    def test_ntp_clock_gating_present(self):
        """
        Firmware must initiate background SNTP configTime() and gate MQTT connection
        until system clock is synchronized (post-2023 epoch).
        """
        self.assertIn("configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2, NTP_SERVER_3);", self.source)
        self.assertIn("bool isClockSynchronized()", self.source)
        self.assertIn("1700000000", self.source)
        self.assertIn("if (!isClockSynchronized())", self.source)

    def test_wifi_ntp_transition_and_timeout_diagnostics(self):
        """
        Firmware must handle Wi-Fi connection transitions cleanly:
        - connectWiFi() must track connection state (wasConnected) so configTime()
          is invoked upon WL_CONNECTED and re-invoked on reconnection.
        - connectMQTT() must emit a rate-limited warning after 30s of failed clock sync.
        """
        self.assertIn("wasConnected", self.source)
        self.assertIn("WiFi.localIP()", self.source)
        self.assertIn("configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2, NTP_SERVER_3);", self.source)
        self.assertIn("[NTP] WARNING: Sync taking >30s", self.source)

    def test_configurable_ntp_servers_supported(self):
        """Configurable NTP servers must be defined with defaults for plant overrides."""
        self.assertIn("NTP_SERVER_1", self.source)
        self.assertIn("NTP_SERVER_2", self.source)
        self.assertIn("NTP_SERVER_3", self.source)

    def test_read_only_invariants_preserved(self):
        """
        The firmware must remain strictly publish-only.
        Zero MQTT subscriptions, zero command callbacks, zero PLC writes.
        """
        self.assertNotIn("mqttClient.subscribe", self.source)
        self.assertNotIn("mqttClient.setCallback", self.source)
        prohibited_write_tokens = ["batchwrite", "randomwrite", "force_coil", "write_register"]
        for token in prohibited_write_tokens:
            self.assertNotIn(token, self.source.lower())

    def test_progmem_substring_helper_and_safe_ca_validation(self):
        """
        Verify that progmem_contains_P is used for PROGMEM substring search with pgm_read_byte,
        and that strstr_P is NOT called on the flash-resident BROKER_ROOT_CA.
        """
        self.assertIn("bool progmem_contains_P(PGM_P haystack, PGM_P needle)", self.source)
        self.assertIn("pgm_read_byte", self.source)
        # Verify strstr_P is no longer invoked on the PROGMEM certificate buffer
        self.assertNotIn("strstr_P(BROKER_ROOT_CA", self.source)
        # Verify both checks use progmem_contains_P
        self.assertIn("progmem_contains_P(BROKER_ROOT_CA, PSTR(\"PASTE_ROOT_CA_CERTIFICATE_HERE\"))", self.source)
        self.assertIn("progmem_contains_P(BROKER_ROOT_CA, PSTR(\"-----BEGIN CERTIFICATE-----\"))", self.source)


class TestProgmemSubstringAlgorithm(unittest.TestCase):
    """
    Host-side algorithmic unit tests for the byte-matching logic of progmem_contains_P.
    NOTE: Host-side tests validate algorithmic correctness only; hardware flash bus safety
    on the ESP8266 is achieved by reading exclusively via pgm_read_byte().
    """

    @staticmethod
    def _host_contains(haystack: str, needle: str) -> bool:
        """Python mirror of the exact byte loop in progmem_contains_P."""
        if not haystack and needle:
            return False
        nlen = len(needle)
        if nlen == 0:
            return True
        hlen = len(haystack)
        if hlen < nlen:
            return False
        for i in range(hlen - nlen + 1):
            match = True
            for j in range(nlen):
                if haystack[i + j] != needle[j]:
                    match = False
                    break
            if match:
                return True
        return False

    def test_matches_at_start(self):
        self.assertTrue(self._host_contains("-----BEGIN CERTIFICATE-----abc", "-----BEGIN CERTIFICATE-----"))

    def test_matches_in_middle(self):
        self.assertTrue(self._host_contains("abcPASTE_ROOT_CA_CERTIFICATE_HEREdef", "PASTE_ROOT_CA_CERTIFICATE_HERE"))

    def test_matches_at_end(self):
        self.assertTrue(self._host_contains("abc-----END CERTIFICATE-----", "-----END CERTIFICATE-----"))

    def test_needle_not_found(self):
        self.assertFalse(self._host_contains("-----BEGIN CERTIFICATE-----real_ca", "PASTE_ROOT_CA_CERTIFICATE_HERE"))

    def test_needle_longer_than_haystack(self):
        self.assertFalse(self._host_contains("short", "much_longer_needle"))

    def test_empty_needle_and_empty_haystack(self):
        self.assertTrue(self._host_contains("anything", ""))
        self.assertFalse(self._host_contains("", "needle"))

    def test_partial_match_then_mismatch_recovers(self):
        self.assertTrue(self._host_contains("-----BEGIN NOT-A-CERT -----BEGIN CERTIFICATE-----", "-----BEGIN CERTIFICATE-----"))


class TestFirmwareTemplateHygiene(unittest.TestCase):
    """
    Validates template hygiene, Git exclusions, and configuration files.
    """

    def test_ca_cert_example_exists_and_formatted(self):
        """ca_cert.h.example must exist with valid PEM delimiters and PROGMEM."""
        self.assertTrue(CA_EXAMPLE_PATH.exists(), f"Missing template: {CA_EXAMPLE_PATH}")
        content = CA_EXAMPLE_PATH.read_text(encoding="utf-8")
        self.assertIn("-----BEGIN CERTIFICATE-----", content)
        self.assertIn("-----END CERTIFICATE-----", content)
        self.assertIn("PASTE_ROOT_CA_CERTIFICATE_HERE", content)
        self.assertIn("PROGMEM", content)
        self.assertIn("FAIL-CLOSED", content)

    def test_gitignore_protects_ca_cert(self):
        """.gitignore must exclude ca_cert.h and **/ca_cert.h."""
        self.assertTrue(GITIGNORE_PATH.exists(), f"Missing .gitignore: {GITIGNORE_PATH}")
        content = GITIGNORE_PATH.read_text(encoding="utf-8")
        self.assertIn("ca_cert.h", content)
        self.assertIn("**/ca_cert.h", content)

    def test_secrets_example_includes_ntp_configuration(self):
        """secrets.h.example must document configurable NTP servers."""
        self.assertTrue(SECRETS_EXAMPLE_PATH.exists())
        content = SECRETS_EXAMPLE_PATH.read_text(encoding="utf-8")
        self.assertIn("NTP_SERVER_1", content)
        self.assertIn("NTP_SERVER_2", content)


class TestTlsVerificationSimulation(unittest.TestCase):
    """
    Software-only TLS verification test harness.
    Validates that client TLS contexts configured with a trusted Root CA
    successfully complete the TLS handshake, while untrusted or mismatched
    CAs strictly fail with SSLCertVerificationError (simulating BearSSL fail-closed policy).
    """

    @classmethod
    def setUpClass(cls):
        # Create an ephemeral loopback TLS server on an arbitrary free port
        cls.server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Load server cert & private key from strings via temp files
        import tempfile
        cls._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(cls._temp_dir.name)

        server_crt_file = temp_path / "server.crt"
        server_key_file = temp_path / "server.key"
        server_crt_file.write_text(TEST_SERVER_CRT_PEM.strip(), encoding="utf-8")
        server_key_file.write_text(TEST_SERVER_KEY_PEM.strip(), encoding="utf-8")

        cls.server_ctx.load_cert_chain(certfile=str(server_crt_file), keyfile=str(server_key_file))

        cls.bind_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.bind_sock.bind(("127.0.0.1", 0))
        cls.port = cls.bind_sock.getsockname()[1]
        cls.bind_sock.listen(5)

        cls._stop_event = threading.Event()

        def _server_worker():
            cls.bind_sock.settimeout(0.5)
            while not cls._stop_event.is_set():
                try:
                    conn, _ = cls.bind_sock.accept()
                except (socket.timeout, OSError):
                    continue
                try:
                    tls_conn = cls.server_ctx.wrap_socket(conn, server_side=True)
                    data = tls_conn.recv(1024)
                    if data:
                        tls_conn.sendall(b"OK_SECURE_CHANNEL")
                    tls_conn.close()
                except Exception:
                    pass

        cls.thread = threading.Thread(target=_server_worker, name="MockTlsBroker", daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls._stop_event.set()
        cls.bind_sock.close()
        cls.thread.join(timeout=1.0)
        cls._temp_dir.cleanup()

    def test_handshake_succeeds_with_trusted_root_ca(self):
        """A client configured with the authoritative Root CA successfully validates the server."""
        client_ctx = ssl.create_default_context(cadata=TEST_ROOT_CA_PEM)
        # Match server subject CN=localhost
        with socket.create_connection(("127.0.0.1", self.port), timeout=2.0) as sock:
            with client_ctx.wrap_socket(sock, server_hostname="localhost") as ssock:
                ssock.sendall(b"PING")
                reply = ssock.recv(1024)
                self.assertEqual(reply, b"OK_SECURE_CHANNEL")

    def test_handshake_fails_closed_with_untrusted_ca(self):
        """A client configured with an untrusted or mismatched CA strictly rejects the connection."""
        client_ctx = ssl.create_default_context(cadata=ROGUE_ROOT_CA_PEM)
        with socket.create_connection(("127.0.0.1", self.port), timeout=2.0) as sock:
            with self.assertRaises(ssl.SSLCertVerificationError) as ctx:
                client_ctx.wrap_socket(sock, server_hostname="localhost")
            self.assertIn("certificate verify failed", str(ctx.exception).lower())

    def test_handshake_fails_closed_with_empty_ca(self):
        """A client without trust anchors configured cannot complete verification."""
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.verify_mode = ssl.CERT_REQUIRED
        client_ctx.check_hostname = True
        with socket.create_connection(("127.0.0.1", self.port), timeout=2.0) as sock:
            with self.assertRaises(ssl.SSLError):
                client_ctx.wrap_socket(sock, server_hostname="localhost")


if __name__ == "__main__":
    unittest.main()
