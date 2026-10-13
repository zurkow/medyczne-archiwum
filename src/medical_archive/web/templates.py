from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

_TEMPLATE_DIRECTORY = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIRECTORY))


def render_template(
    request: Request,
    name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )
