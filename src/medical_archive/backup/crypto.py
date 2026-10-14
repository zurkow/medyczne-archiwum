import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"MARCHIVE"
FORMAT_VERSION = 1
SALT_BYTES = 16
NONCE_BYTES = 12
HEADER_BYTES = len(MAGIC) + 3 + SALT_BYTES + NONCE_BYTES
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
MAX_ENVELOPE_BYTES = 1024 * 1024 * 1024

_TAG_BYTES = 16


class InvalidBackup(Exception):
    """Raised when a backup envelope or archive is malformed, unsupported or unsafe."""


class InvalidBackupPassword(InvalidBackup):
    """Raised when a backup cannot be decrypted with the given password."""


def encrypt(payload: bytes, password: str) -> bytes:
    if HEADER_BYTES + len(payload) + _TAG_BYTES > MAX_ENVELOPE_BYTES:
        raise ValueError("Backup payload exceeds the maximum envelope size.")
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = MAGIC + bytes((FORMAT_VERSION, SALT_BYTES, NONCE_BYTES)) + salt + nonce
    ciphertext = AESGCM(_derive_key(password, salt)).encrypt(nonce, payload, header)
    return header + ciphertext


def decrypt(envelope: bytes, password: str) -> bytes:
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise InvalidBackup("Backup envelope exceeds the maximum size.")
    if len(envelope) < HEADER_BYTES + _TAG_BYTES:
        raise InvalidBackup("Backup envelope is truncated.")
    magic_end = len(MAGIC)
    if (
        envelope[:magic_end] != MAGIC
        or envelope[magic_end] != FORMAT_VERSION
        or envelope[magic_end + 1] != SALT_BYTES
        or envelope[magic_end + 2] != NONCE_BYTES
    ):
        raise InvalidBackup("Backup envelope header is not supported.")
    salt_start = magic_end + 3
    nonce_start = salt_start + SALT_BYTES
    salt = envelope[salt_start:nonce_start]
    nonce = envelope[nonce_start:HEADER_BYTES]
    key = _derive_key(password, salt)
    try:
        return AESGCM(key).decrypt(nonce, envelope[HEADER_BYTES:], envelope[:HEADER_BYTES])
    except InvalidTag:
        raise InvalidBackupPassword("Backup password is wrong or the backup is damaged.") from None


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=KEY_BYTES,
    )
