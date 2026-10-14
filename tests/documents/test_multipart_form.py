import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest
from starlette.requests import Request

from medical_archive.documents.storage import (
    MultipartForm,
    UploadError,
    read_multipart_form,
)

_BOUNDARY = "synthetic-boundary"
_TEXT_FIELDS = frozenset({"csrf_token", "confirm"})


def _text_part(name: str, value: str) -> bytes:
    return (
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
    ).encode()


def _file_part(content: bytes, *, name: str = "backup_file") -> bytes:
    return (
        (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="synthetic.marchive"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + b"\r\n"
    )


def _closing() -> bytes:
    return f"--{_BOUNDARY}--\r\n".encode()


def _request(
    body: bytes,
    *,
    chunk_size: int = 7,
    content_length: int | None = None,
    forbid_receive: bool = False,
) -> Request:
    headers = [(b"content-type", f"multipart/form-data; boundary={_BOUNDARY}".encode())]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    chunks = [body[offset : offset + chunk_size] for offset in range(0, len(body), chunk_size)]
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict:
        if forbid_receive:
            pytest.fail("The request body must not be read.")
        return messages.pop(0)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    return Request(scope, receive)


def _parse(
    request: Request,
    destination: Path,
    *,
    max_file_bytes: int = 1024,
    before_file=lambda fields: None,
) -> MultipartForm:
    return asyncio.run(
        read_multipart_form(
            request,
            text_fields=_TEXT_FIELDS,
            file_field="backup_file",
            destination=destination,
            max_file_bytes=max_file_bytes,
            before_file=before_file,
        )
    )


def test_multipart_form_streams_file_to_disk_and_returns_allowed_text_fields(tmp_path: Path):
    content = bytes(range(256)) * 3
    body = (
        _text_part("csrf_token", "synthetic-token")
        + _text_part("confirm", "yes")
        + _file_part(content)
        + _closing()
    )
    destination = tmp_path / "upload.marchive"

    form = _parse(_request(body, chunk_size=13), destination)

    assert form.fields == {"csrf_token": "synthetic-token", "confirm": "yes"}
    assert form.file_size == len(content)
    assert destination.read_bytes() == content


def test_multipart_form_calls_before_file_before_writing_any_file_bytes(tmp_path: Path):
    seen: list[dict[str, str]] = []

    def reject(fields: Mapping[str, str]) -> None:
        seen.append(dict(fields))
        assert not destination.exists()
        raise UploadError(403, "Invalid or missing CSRF token.")

    body = _text_part("csrf_token", "wrong") + _file_part(b"secret bytes") + _closing()
    destination = tmp_path / "upload.marchive"

    with pytest.raises(UploadError) as error:
        _parse(_request(body), destination, before_file=reject)

    assert error.value.status_code == 403
    assert seen == [{"csrf_token": "wrong"}]
    assert not destination.exists()


@pytest.mark.parametrize(
    "parts",
    [
        _text_part("unexpected", "value"),
        _text_part("csrf_token", "one") + _text_part("csrf_token", "two"),
        _file_part(b"first") + _file_part(b"second"),
    ],
    ids=["unknown", "duplicate-text", "duplicate-file"],
)
def test_multipart_form_rejects_unknown_or_duplicate_text_fields(tmp_path: Path, parts: bytes):
    destination = tmp_path / "upload.marchive"

    with pytest.raises(UploadError) as error:
        _parse(_request(parts + _file_part(b"data") + _closing()), destination)

    assert error.value.status_code == 400
    assert not destination.exists()


def test_multipart_form_rejects_file_over_cap_and_removes_partial_file(tmp_path: Path):
    body = _text_part("csrf_token", "token") + _file_part(b"x" * 200) + _closing()
    destination = tmp_path / "upload.marchive"

    with pytest.raises(UploadError) as error:
        _parse(_request(body), destination, max_file_bytes=100)

    assert error.value.status_code == 413
    assert not destination.exists()


def test_multipart_form_rejects_oversized_content_length_before_reading_body(tmp_path: Path):
    destination = tmp_path / "upload.marchive"
    request = _request(b"", content_length=1024 + 64 * 1024 + 1, forbid_receive=True)

    with pytest.raises(UploadError) as error:
        _parse(request, destination)

    assert error.value.status_code == 413
    assert not destination.exists()
