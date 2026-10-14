import pytest

from medical_archive.backup import crypto
from medical_archive.backup.crypto import InvalidBackup, InvalidBackupPassword


def test_backup_uses_fresh_salt_and_nonce_and_roundtrips():
    payload = b"synthetic backup payload"

    first = crypto.encrypt(payload, "correct horse battery staple")
    second = crypto.encrypt(payload, "correct horse battery staple")

    assert first != second
    assert crypto.decrypt(first, "correct horse battery staple") == payload
    assert crypto.decrypt(second, "correct horse battery staple") == payload


def test_backup_rejects_wrong_password_and_modified_ciphertext():
    encrypted = crypto.encrypt(b"synthetic payload", "correct horse battery staple")

    with pytest.raises(InvalidBackupPassword):
        crypto.decrypt(encrypted, "incorrect password")

    modified = bytearray(encrypted)
    modified[-1] ^= 1
    with pytest.raises(InvalidBackupPassword):
        crypto.decrypt(bytes(modified), "correct horse battery staple")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda envelope: envelope[:12],
        lambda envelope: b"X" + envelope[1:],
        lambda envelope: envelope[:8] + b"\x02" + envelope[9:],
        lambda envelope: envelope[:9] + b"\x0f" + envelope[10:],
        lambda envelope: envelope[:10] + b"\x0b" + envelope[11:],
    ],
    ids=["truncated", "bad-magic", "unsupported-version", "bad-salt-length", "bad-nonce-length"],
)
def test_backup_rejects_invalid_or_truncated_envelopes(mutate):
    encrypted = crypto.encrypt(b"synthetic payload", "test password")

    with pytest.raises(InvalidBackup):
        crypto.decrypt(mutate(encrypted), "test password")


def test_backup_rejects_envelope_over_size_limit(monkeypatch: pytest.MonkeyPatch):
    encrypted = crypto.encrypt(b"synthetic payload", "test password")
    monkeypatch.setattr(crypto, "MAX_ENVELOPE_BYTES", len(encrypted) - 1)

    with pytest.raises(InvalidBackup):
        crypto.decrypt(encrypted, "test password")
