"""
Generate a self-signed TLS cert + key for local LAN demos.

We need HTTPS because browsers refuse `getUserMedia` on plain HTTP from a LAN
IP (it's not a "secure context"). The cert covers localhost, 127.0.0.1, plus
every LAN IPv4 address Windows reports for this machine, so the same cert
works whether someone hits the server via 127.0.0.1, hostname, or LAN IP.

Run once:  python scripts/make_cert.py
The certs end up in certs/dev.crt and certs/dev.key (both git-ignored).
"""
from __future__ import annotations
import datetime as dt
import ipaddress
import socket
import subprocess
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.stderr.write(
        "Missing dependency: pip install cryptography\n"
        "(it's already a transitive dep of claude-agent-sdk, so this should be there)\n"
    )
    sys.exit(1)


CERT_DIR = Path(__file__).resolve().parent.parent / "certs"
CERT_FILE = CERT_DIR / "dev.crt"
KEY_FILE = CERT_DIR / "dev.key"


def lan_ipv4s() -> list[str]:
    """Best-effort: ask Windows for every non-loopback IPv4 it has."""
    ips: set[str] = set()
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetIPAddress -AddressFamily IPv4 | "
                 "Where-Object { $_.IPAddress -notlike '127.*' -and "
                 "$_.IPAddress -notlike '169.254.*' }).IPAddress"],
                stderr=subprocess.DEVNULL, text=True, timeout=5,
            )
            ips.update(s.strip() for s in out.splitlines() if s.strip())
        except Exception:
            pass
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None):
            ip = entry[4][0]
            if "." in ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def make_cert() -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    hostname = socket.gethostname()
    ips = lan_ipv4s()

    san: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for ip in ips:
        try:
            san.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
        except Exception:
            pass

    now = dt.datetime.now(dt.timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "FlexiVoice AI (dev)"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FlexiLoans"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Wrote {CERT_FILE}")
    print(f"Wrote {KEY_FILE}")
    print(f"SANs: localhost, {hostname}, 127.0.0.1" + (", " + ", ".join(ips) if ips else ""))
    print("\nTo serve over HTTPS:")
    print("  set USE_TLS=1 before running run.bat, or pass")
    print("  --ssl-keyfile certs/dev.key --ssl-certfile certs/dev.crt to uvicorn directly.")


if __name__ == "__main__":
    make_cert()
