import os
import re
import tempfile
from email.message import Message
from pathlib import Path

from fastapi import Request
from starlette.requests import ClientDisconnect

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_MAX_HEADERS_BYTES = 16_384
_READ_CHUNK_BYTES = 64 * 1024
_SIGNATURES = {
    ".pdf": (b"%PDF-", "application/pdf"),
    ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
    ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
    ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
}
_FILENAME_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class UploadError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class DocumentStorageError(Exception):
    """Raised when a private original cannot be persisted safely."""


async def read_multipart_file(request: Request) -> tuple[str, str, bytes]:
    content_type = request.headers.get("content-type", "")
    content_header = Message()
    content_header["content-type"] = content_type
    boundary = content_header.get_param("boundary", header="content-type")
    if (
        content_header.get_content_type() != "multipart/form-data"
        or not isinstance(boundary, str)
        or not boundary
        or len(boundary) > 70
        or any(ord(character) < 33 or ord(character) > 126 for character in boundary)
    ):
        raise UploadError(415, "Upload must use multipart form data.")

    opening_boundary = b"--" + boundary.encode("ascii") + b"\r\n"
    body_boundary = b"\r\n--" + boundary.encode("ascii")
    buffer = bytearray()
    content = bytearray()
    state = "opening"
    filename: str | None = None
    declared_mime: str | None = None
    finished = False
    trailing_bytes = bytearray()

    try:
        async for incoming_chunk in request.stream():
            if not incoming_chunk:
                continue
            for offset in range(0, len(incoming_chunk), _READ_CHUNK_BYTES):
                chunk = incoming_chunk[offset : offset + _READ_CHUNK_BYTES]
                if finished:
                    trailing_bytes.extend(chunk)
                    if len(trailing_bytes) > 2 or not b"\r\n".startswith(trailing_bytes):
                        raise UploadError(400, "Unexpected data after the uploaded file.")
                    continue
                buffer.extend(chunk)

                while True:
                    if state == "opening":
                        if len(buffer) < len(opening_boundary):
                            break
                        if not buffer.startswith(opening_boundary):
                            raise UploadError(400, "Invalid multipart upload.")
                        del buffer[: len(opening_boundary)]
                        state = "headers"
                    elif state == "headers":
                        separator = buffer.find(b"\r\n\r\n")
                        if separator < 0:
                            if len(buffer) > _MAX_HEADERS_BYTES:
                                raise UploadError(400, "Upload headers are too large.")
                            break
                        if separator > _MAX_HEADERS_BYTES:
                            raise UploadError(400, "Upload headers are too large.")
                        filename, declared_mime = _read_file_headers(bytes(buffer[:separator]))
                        del buffer[: separator + 4]
                        state = "file"
                    else:
                        boundary_index = buffer.find(body_boundary)
                        if boundary_index < 0:
                            retained_bytes = len(body_boundary) + 2
                            safe_length = max(0, len(buffer) - retained_bytes)
                            _append_bounded(content, buffer[:safe_length])
                            del buffer[:safe_length]
                            break

                        marker_index = boundary_index + len(body_boundary)
                        if len(buffer) < marker_index + 2:
                            _append_bounded(content, buffer[:boundary_index])
                            del buffer[:boundary_index]
                            break
                        marker = bytes(buffer[marker_index : marker_index + 2])
                        if marker not in {b"--", b"\r\n"}:
                            safe_length = boundary_index + 2
                            _append_bounded(content, buffer[:safe_length])
                            del buffer[:safe_length]
                            continue
                        _append_bounded(content, buffer[:boundary_index])
                        remainder = bytes(buffer[marker_index + 2 :])
                        if marker != b"--":
                            raise UploadError(400, "Only a single uploaded file is accepted.")
                        if len(remainder) > 2 or not b"\r\n".startswith(remainder):
                            raise UploadError(400, "Unexpected multipart data.")
                        trailing_bytes.extend(remainder)
                        buffer.clear()
                        state = "finished"
                        finished = True
                        break
    except ClientDisconnect as error:
        raise UploadError(400, "The uploaded file was interrupted.") from error

    if state != "finished" or filename is None or declared_mime is None:
        raise UploadError(400, "The uploaded file is incomplete.")
    return filename, declared_mime, bytes(content)


def _read_file_headers(raw_headers: bytes) -> tuple[str, str]:
    parsed_headers = Message()
    for line in raw_headers.decode("latin-1").split("\r\n"):
        name, separator, value = line.partition(":")
        if not separator or not name or name.lower() in parsed_headers:
            raise UploadError(400, "Invalid upload headers.")
        parsed_headers[name] = value.strip()

    disposition = parsed_headers.get("content-disposition")
    if disposition is None:
        raise UploadError(400, "The upload is missing file metadata.")
    disposition_header = Message()
    disposition_header["content-disposition"] = disposition
    if (
        disposition_header.get_content_disposition() != "form-data"
        or disposition_header.get_param("name", header="content-disposition") != "file"
    ):
        raise UploadError(400, "Upload must contain one file field.")
    filename = disposition_header.get_filename()
    if not filename:
        raise UploadError(400, "The upload filename is missing.")
    declared_mime = parsed_headers.get("content-type", "application/octet-stream")
    return filename, declared_mime.lower()


def _append_bounded(content: bytearray, chunk: bytes | bytearray) -> None:
    if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
        raise UploadError(413, "The uploaded file exceeds the 25 MiB limit.")
    content.extend(chunk)


def validate_upload(filename: str, declared_mime: str, content: bytes) -> tuple[str, str]:
    safe_filename = _FILENAME_CONTROL_CHARACTERS.sub(
        "", filename.replace("\\", "/").rsplit("/", 1)[-1]
    )
    if not safe_filename or len(safe_filename.encode("utf-8")) > 255:
        raise UploadError(422, "The filename is invalid.")

    signature = _SIGNATURES.get(Path(safe_filename).suffix.lower())
    if signature is None:
        raise UploadError(415, "Only PDF, JPEG, and PNG files are supported.")
    expected_signature, expected_mime = signature
    if not content.startswith(expected_signature) or declared_mime != expected_mime:
        raise UploadError(415, "The filename, file signature, and MIME type must match.")
    if not content:
        raise UploadError(422, "The uploaded file is empty.")
    return safe_filename, expected_mime


def write_document(
    data_dir: Path,
    *,
    static_dir: Path,
    storage_key: str,
    content: bytes,
) -> Path:
    temporary_path: Path | None = None
    try:
        document_dir = (data_dir / "documents").resolve()
        webroot = static_dir.resolve()
        if document_dir == webroot or webroot in document_dir.parents:
            raise DocumentStorageError("Document storage must be outside the webroot.")
        final_path = document_dir / storage_key
        document_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=document_dir,
            prefix=".upload-",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, final_path)
        return final_path
    except DocumentStorageError:
        _remove_temporary_file(temporary_path)
        raise
    except OSError as error:
        _remove_temporary_file(temporary_path)
        raise DocumentStorageError from error


def _remove_temporary_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
