"""Persistent, private local CA and IP-specific HTTPS certificates for DropOnce."""

import ipaddress
import os
from pathlib import Path
import ssl
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def identity_folder():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "DropOnce" / "tls"


def _save_private(path, content):
    # Never write a private key with world-readable permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content)


def _key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _cert_bytes(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _check_private(path):
    if path.is_symlink() or (os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077):
        raise ValueError(f"Unsafe permissions on TLS private key: {path}")


@contextmanager
def _identity_lock(directory):
    """Serialize identity creation between app windows, including separate processes."""
    lockfile = directory / ".identity.lock"
    if lockfile.is_symlink():
        raise ValueError("TLS identity lock cannot be a symlink")
    fd = os.open(lockfile, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _check_private(lockfile)
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _tls_context_locked(directory, ip):
    root_key = directory / "root-key.pem"
    root_cert = directory / "root-cert.pem"
    if root_cert.exists() and not root_key.exists():
        raise ValueError("DropOnce CA private key is missing; restore a backup or set up a new phone certificate")
    if not root_cert.exists():
        if root_key.exists():
            _check_private(root_key)
            key = serialization.load_pem_private_key(root_key.read_bytes(), password=None)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DropOnce local transfer CA")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=365 * 3))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
                                             content_commitment=False, data_encipherment=False,
                                             key_agreement=False, key_cert_sign=True, crl_sign=True,
                                             encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        if not root_key.exists():
            _save_private(root_key, _key_bytes(key))
        _save_private(root_cert, _cert_bytes(cert))
    _check_private(root_key)
    key = serialization.load_pem_private_key(root_key.read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(root_cert.read_bytes())
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ValueError("Local TLS authority key and certificate do not match")
    now = datetime.now(timezone.utc)
    if (not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or cert.not_valid_after_utc <= now + timedelta(days=1)):
        raise ValueError("Local certificate authority is invalid or expired; back up and remove its identity folder")
    leaf_key_path = directory / f"{ip}-key.pem"
    leaf_cert_path = directory / f"{ip}-cert.pem"
    renew = not leaf_key_path.exists() or not leaf_cert_path.exists()
    if not renew:
        _check_private(leaf_key_path)
        leaf = x509.load_pem_x509_certificate(leaf_cert_path.read_bytes())
        renew = leaf.not_valid_after_utc <= now + timedelta(days=1)
        try:
            leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
            leaf.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            renew = True
    if renew:
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        leaf = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(ip))]))
                .issuer_name(cert.subject).public_key(leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(min(now + timedelta(days=365), cert.not_valid_after_utc))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
                               critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                               critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(key, hashes.SHA256()))
        for path, data in ((leaf_key_path, _key_bytes(leaf_key)),
                           (leaf_cert_path, _cert_bytes(leaf))):
            if path.exists():
                path.unlink()
            _save_private(path, data)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(leaf_cert_path), str(leaf_key_path))
    return context, root_cert


def tls_context(address):
    """Return an HTTPS server context and the local CA certificate path."""
    ip = ipaddress.ip_address(address)
    directory = identity_folder()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or (os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) & 0o077):
        raise ValueError("TLS identity folder must be private and cannot be a symlink")
    with _identity_lock(directory):
        return _tls_context_locked(directory, ip)
