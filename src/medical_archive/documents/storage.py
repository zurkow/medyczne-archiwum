import os
import re
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

from fastapi import Request
from starlette.requests import ClientDisconnect

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_MAX_HEADERS_BYTES = 16_384
_READ_CHUNK_BYTES = 64 * 1024
_FORM_OVERHEAD_BYTES = 64 * 1024
_PART_HEADERS = "headers"
_PART_DATA = "data"
_PART_NEXT = "next"
_PART_END = "end"
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


@dataclass(frozen=True)
class MultipartForm:
    fields: dict[str, str]
    file_size: int


async def read_multipart_file(request: Request) -> tuple[str, str, bytes]:
    boundary = _multipart_boundary(request)
    content = bytearray()
    filename: str | None = None
    declared_mime: str | None = None
    finished = False

    async with aclosing(_iter_multipart_events(request, boundary)) as events:
        async for event, payload in events:
            if event == _PART_HEADERS:
                filename, declared_mime = _read_file_headers(payload)
            elif event == _PART_DATA:
                _append_bounded(content, payload)
            elif event == _PART_NEXT:
                raise UploadError(400, "Only a single uploaded file is accepted.")
            else:
                finished = True

    if not finished or filename is None or declared_mime is None:
        raise UploadError(400, "The uploaded file is incomplete.")
    return filename, declared_mime, bytes(content)


async def read_multipart_form(
    request: Request,
    *,
    text_fields: frozenset[str],
    file_field: str,
    destination: Path,
    max_file_bytes: int,
    max_field_bytes: int = 4096,
    before_file: Callable[[Mapping[str, str]], None],
) -> MultipartForm:
    boundary = _multipart_boundary(request)
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > max_file_bytes + _FORM_OVERHEAD_BYTES:
        raise UploadError(413, "The uploaded file is too large.")

    fields: dict[str, str] = {}
    field_name: str | None = None
    field_value = bytearray()
    file_handle: BinaryIO | None = None
    file_created = False
    file_size = 0
    part_count = 0
    finished = False
    try:
        async with aclosing(_iter_multipart_events(request, boundary)) as events:
            async for event, payload in events:
                if event == _PART_HEADERS:
                    part_count += 1
                    if part_count > len(text_fields) + 1:
                        raise UploadError(400, "The form has too many parts.")
                    name, has_filename = _read_form_part_headers(payload)
                    if name == file_field:
                        if file_created:
                            raise UploadError(400, "Only a single uploaded file is accepted.")
                        before_file(MappingProxyType(dict(fields)))
                        file_handle = destination.open("xb")
                        file_created = True
                    elif name in text_fields and name not in fields and not has_filename:
                        field_name = name
                    else:
                        raise UploadError(400, "Unexpected form field.")
                elif event == _PART_DATA:
                    if file_handle is not None:
                        file_size += len(payload)
                        if file_size > max_file_bytes:
                            raise UploadError(413, "The uploaded file is too large.")
                        file_handle.write(payload)
                    elif field_name is not None:
                        if len(field_value) + len(payload) > max_field_bytes:
                            raise UploadError(413, "A form field is too large.")
                        field_value.extend(payload)
                else:
                    if field_name is not None:
                        fields[field_name] = _decode_field(field_value)
                        field_name = None
                        field_value.clear()
                    if file_handle is not None:
                        file_handle.close()
                        file_handle = None
                    finished = event == _PART_END
        if not finished:
            raise UploadError(400, "The uploaded file is incomplete.")
        if not file_created:
            raise UploadError(400, "The upload is missing the file.")
    except BaseException:
        if file_handle is not None:
            file_handle.close()
        if file_created:
            _remove_temporary_file(destination)
        raise
    return MultipartForm(fields=fields, file_size=file_size)


def _multipart_boundary(request: Request) -> str:
    content_header = Message()
    content_header["content-type"] = request.headers.get("content-type", "")
    boundary = content_header.get_param("boundary", header="content-type")
    if (
        content_header.get_content_type() != "multipart/form-data"
        or not isinstance(boundary, str)
        or not boundary
        or len(boundary) > 70
        or any(ord(character) < 33 or ord(character) > 126 for character in boundary)
    ):
        raise UploadError(415, "Upload must use multipart form data.")
    return boundary


async def _iter_multipart_events(
    request: Request,
    boundary: str,
) -> AsyncIterator[tuple[str, bytes]]:
    opening_boundary = b"--" + boundary.encode("ascii") + b"\r\n"
    body_boundary = b"\r\n--" + boundary.encode("ascii")
    buffer = bytearray()
    state = "opening"
    trailing_bytes = bytearray()

    try:
        async for incoming_chunk in request.stream():
            if not incoming_chunk:
                continue
            for offset in range(0, len(incoming_chunk), _READ_CHUNK_BYTES):
                chunk = incoming_chunk[offset : offset + _READ_CHUNK_BYTES]
                if state == "finished":
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
                        yield _PART_HEADERS, bytes(buffer[:separator])
                        del buffer[: separator + 4]
                        state = "data"
                    else:
                        boundary_index = buffer.find(body_boundary)
                        if boundary_index < 0:
                            retained_bytes = len(body_boundary) + 2
                            safe_length = max(0, len(buffer) - retained_bytes)
                            if safe_length:
                                yield _PART_DATA, bytes(buffer[:safe_length])
                            del buffer[:safe_length]
                            break

                        marker_index = boundary_index + len(body_boundary)
                        if len(buffer) < marker_index + 2:
                            if boundary_index:
                                yield _PART_DATA, bytes(buffer[:boundary_index])
                            del buffer[:boundary_index]
                            break
                        marker = bytes(buffer[marker_index : marker_index + 2])
                        if marker not in {b"--", b"\r\n"}:
                            safe_length = boundary_index + 2
                            yield _PART_DATA, bytes(buffer[:safe_length])
                            del buffer[:safe_length]
                            continue
                        if boundary_index:
                            yield _PART_DATA, bytes(buffer[:boundary_index])
                        if marker == b"\r\n":
                            yield _PART_NEXT, b""
                            del buffer[: marker_index + 2]
                            state = "headers"
                            continue
                        remainder = bytes(buffer[marker_index + 2 :])
                        if len(remainder) > 2 or not b"\r\n".startswith(remainder):
                            raise UploadError(400, "Unexpected multipart data.")
                        trailing_bytes.extend(remainder)
                        buffer.clear()
                        state = "finished"
                        yield _PART_END, b""
                        break
    except ClientDisconnect as error:
        raise UploadError(400, "The uploaded file was interrupted.") from error


def _parse_part_headers(raw_headers: bytes) -> Message:
    parsed_headers = Message()
    for line in raw_headers.decode("latin-1").split("\r\n"):
        name, separator, value = line.partition(":")
        if not separator or not name or name.lower() in parsed_headers:
            raise UploadError(400, "Invalid upload headers.")
        parsed_headers[name] = value.strip()
    return parsed_headers


def _read_file_headers(raw_headers: bytes) -> tuple[str, str]:
    parsed_headers = _parse_part_headers(raw_headers)
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


def _read_form_part_headers(raw_headers: bytes) -> tuple[str, bool]:
    disposition_header = Message()
    disposition_header["content-disposition"] = _parse_part_headers(raw_headers).get(
        "content-disposition", ""
    )
    name = disposition_header.get_param("name", header="content-disposition")
    if (
        disposition_header.get_content_disposition() != "form-data"
        or not isinstance(name, str)
        or not name
    ):
        raise UploadError(400, "Invalid form part.")
    has_filename = disposition_header.get_param("filename", header="content-disposition")
    return name, has_filename is not None


def _decode_field(value: bytearray) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UploadError(400, "Form fields must be valid UTF-8.") from error


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
