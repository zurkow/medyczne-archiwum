import hashlib
import io
import json
import re
import shutil
import stat
import uuid
import zipfile
import zlib
from collections.abc import Sequence
from pathlib import Path

from medical_archive.backup import crypto
from medical_archive.backup.crypto import InvalidBackup

MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.sqlite3"
FILES_PREFIX = "files/"
ARCHIVE_FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024

_MAX_FILE_ENTRIES = 100_000
_CHUNK_BYTES = 64 * 1024
_MANIFEST_KEYS = frozenset(
    {"format_version", "archive_id", "schema_revision", "database", "storage_keys", "files"}
)
_DIGEST_KEYS = frozenset({"size", "sha256"})
_REVISION_PATTERN = re.compile(r"[0-9A-Za-z_]{1,64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_ENCRYPTED_FLAG = 0x1


def is_storage_key(value: object) -> bool:
    return isinstance(value, str) and _is_canonical_uuid(value)


def write_archive(
    target: Path,
    *,
    database_path: Path,
    documents_dir: Path,
    storage_keys: Sequence[str],
    schema_revision: str,
) -> dict:
    keys = sorted(storage_keys)
    with zipfile.ZipFile(target, "x") as archive:
        database_digest = _write_entry(
            archive, DATABASE_NAME, database_path, compress_type=zipfile.ZIP_DEFLATED
        )
        files = {
            key: _write_entry(
                archive, FILES_PREFIX + key, documents_dir / key, compress_type=zipfile.ZIP_STORED
            )
            for key in keys
        }
        manifest = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "archive_id": str(uuid.uuid4()),
            "schema_revision": schema_revision,
            "database": database_digest,
            "storage_keys": keys,
            "files": files,
        }
        archive.writestr(
            _new_entry_info(MANIFEST_NAME, compress_type=zipfile.ZIP_DEFLATED),
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
    return manifest


def validate_archive(payload: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return _validate_open_archive(archive)
    except (
        zipfile.BadZipFile,
        json.JSONDecodeError,
        UnicodeDecodeError,
        EOFError,
        RecursionError,
        zlib.error,
    ):
        raise InvalidBackup("Backup archive is malformed.") from None


def extract_archive(payload: bytes, destination: Path) -> dict:
    manifest = validate_archive(payload)
    documents_dir = destination / "documents"
    documents_dir.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        _extract_entry(archive, DATABASE_NAME, destination / DATABASE_NAME)
        for key in manifest["storage_keys"]:
            _extract_entry(archive, FILES_PREFIX + key, documents_dir / key)
    return manifest


def _new_entry_info(name: str, *, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = compress_type
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _write_entry(
    archive: zipfile.ZipFile,
    name: str,
    source: Path,
    *,
    compress_type: int,
) -> dict:
    digest = hashlib.sha256()
    size = 0
    with (
        source.open("rb") as reader,
        archive.open(_new_entry_info(name, compress_type=compress_type), "w") as writer,
    ):
        while chunk := reader.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def _extract_entry(archive: zipfile.ZipFile, name: str, target: Path) -> None:
    with archive.open(name) as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, _CHUNK_BYTES)


def _validate_open_archive(archive: zipfile.ZipFile) -> dict:
    entries = archive.infolist()
    if len(entries) > 2 + _MAX_FILE_ENTRIES:
        raise InvalidBackup("Backup archive has too many entries.")
    names: set[str] = set()
    for info in entries:
        _check_entry(info, names)
        names.add(info.filename)

    if MANIFEST_NAME not in names:
        raise InvalidBackup("Backup archive has no manifest.")
    manifest = _read_manifest(archive)

    expected = {DATABASE_NAME: manifest["database"]}
    expected.update(
        {FILES_PREFIX + key: manifest["files"][key] for key in manifest["storage_keys"]}
    )
    if names != {MANIFEST_NAME, *expected}:
        raise InvalidBackup("Backup archive entries do not match the manifest.")

    total_size = 0
    for name, declared in expected.items():
        if archive.getinfo(name).file_size != declared["size"]:
            raise InvalidBackup("Backup archive entry size does not match the manifest.")
        total_size += declared["size"]
    if total_size > crypto.MAX_ENVELOPE_BYTES:
        raise InvalidBackup("Backup archive content exceeds the maximum size.")
    for name, declared in expected.items():
        _check_entry_content(archive, name, declared)
    return manifest


def _check_entry(info: zipfile.ZipInfo, seen_names: set[str]) -> None:
    if info.orig_filename != info.filename or not _is_allowed_name(info.filename):
        raise InvalidBackup("Backup archive contains an unexpected entry.")
    if info.filename in seen_names:
        raise InvalidBackup("Backup archive contains a duplicate entry.")
    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
        raise InvalidBackup("Backup archive contains a non-file entry.")
    if info.flag_bits & _ENCRYPTED_FLAG or info.compress_type not in _ALLOWED_COMPRESSION:
        raise InvalidBackup("Backup archive entry uses an unsupported encoding.")


def _is_allowed_name(name: str) -> bool:
    if name in (MANIFEST_NAME, DATABASE_NAME):
        return True
    return name.startswith(FILES_PREFIX) and _is_canonical_uuid(name[len(FILES_PREFIX) :])


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    limit = MAX_MANIFEST_BYTES
    if archive.getinfo(MANIFEST_NAME).file_size > limit:
        raise InvalidBackup("Backup manifest exceeds the maximum size.")
    with archive.open(MANIFEST_NAME) as reader:
        raw = reader.read(limit + 1)
    if len(raw) > limit:
        raise InvalidBackup("Backup manifest exceeds the maximum size.")
    return _parse_manifest(json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates))


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise InvalidBackup("Backup manifest contains duplicate keys.")
    return result


def _parse_manifest(value: object) -> dict:
    if not (
        isinstance(value, dict)
        and set(value) == _MANIFEST_KEYS
        and type(value["format_version"]) is int
        and value["format_version"] == ARCHIVE_FORMAT_VERSION
        and _is_archive_id(value["archive_id"])
        and isinstance(value["schema_revision"], str)
        and _REVISION_PATTERN.fullmatch(value["schema_revision"])
        and _is_digest(value["database"])
        and _is_storage_key_list(value["storage_keys"])
        and isinstance(value["files"], dict)
        and set(value["files"]) == set(value["storage_keys"])
        and all(_is_digest(digest) for digest in value["files"].values())
    ):
        raise InvalidBackup("Backup manifest is invalid.")
    return value


def _is_archive_id(value: object) -> bool:
    return is_storage_key(value) and uuid.UUID(value).version == 4


def _is_storage_key_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(is_storage_key(key) for key in value)
        and value == sorted(set(value))
    )


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _DIGEST_KEYS
        and type(value["size"]) is int
        and value["size"] >= 0
        and isinstance(value["sha256"], str)
        and _SHA256_PATTERN.fullmatch(value["sha256"]) is not None
    )


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _check_entry_content(archive: zipfile.ZipFile, name: str, declared: dict) -> None:
    digest = hashlib.sha256()
    remaining = declared["size"] + 1
    read = 0
    with archive.open(name) as reader:
        while remaining > 0 and (chunk := reader.read(min(_CHUNK_BYTES, remaining))):
            digest.update(chunk)
            read += len(chunk)
            remaining -= len(chunk)
    if read != declared["size"] or digest.hexdigest() != declared["sha256"]:
        raise InvalidBackup("Backup archive entry does not match the manifest.")
