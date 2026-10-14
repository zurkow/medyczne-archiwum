from urllib.parse import parse_qs

from fastapi import HTTPException, Request


async def read_form_fields(
    request: Request,
    *,
    max_bytes: int = 16_384,
    max_fields: int = 16,
) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0]
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Unsupported form encoding.")
    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="Form data is too large.")
    try:
        parsed = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=max_fields,
            strict_parsing=False,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid form data.") from error
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(status_code=400, detail="Duplicate form fields are not allowed.")
    return {key: values[0] for key, values in parsed.items()}
