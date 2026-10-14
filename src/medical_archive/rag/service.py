import json
import logging
from dataclasses import dataclass
from time import perf_counter
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from medical_archive.rag.prompt import build_prompt
from medical_archive.rag.retrieval import RetrievedSource

logger = logging.getLogger(__name__)
NO_DATA_ANSWER = "Nie znalazłem w dostępnych źródłach informacji, która odpowiada na to pytanie."


class AnswerServiceError(RuntimeError):
    status_code = 502
    public_message = "Nie udało się zweryfikować odpowiedzi asystenta."


class GenerationError(AnswerServiceError):
    status_code = 503
    public_message = "Lokalny model jest niedostępny. Spróbuj ponownie później."


class InvalidModelResponseError(AnswerServiceError):
    public_message = "Lokalny model zwrócił odpowiedź, której nie można bezpiecznie zweryfikować."


class InvalidCitationError(InvalidModelResponseError):
    public_message = "Lokalny model zwrócił nieprawidłowe cytowanie."


@dataclass(frozen=True)
class AnswerSource:
    citation_id: str
    document_id: str
    filename: str
    page_number: int
    url: str


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    sources: list[AnswerSource]


class LMStudioClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 90.0,
        request=None,
    ) -> None:
        parsed_url = urlsplit(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("The chat model must use a loopback LM Studio URL.")
        if not model.strip():
            raise ValueError("A local chat model name must be configured.")
        if timeout <= 0:
            raise ValueError("The local chat model timeout must be positive.")
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.timeout = timeout
        self._request = request or self._request_local_service

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Odpowiadaj wyłącznie w formacie wymaganym przez instrukcję.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            result = self._request(self.endpoint, payload, self.timeout)
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError
            return content
        except GenerationError:
            raise
        except Exception as error:
            raise GenerationError("The local model request failed.") from error

    @staticmethod
    def _request_local_service(endpoint: str, payload: dict[str, object], timeout: float):
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as error:
            raise GenerationError("The local model request failed.") from error


class AnswerService:
    def __init__(self, *, retriever, generator) -> None:
        self.retriever = retriever
        self.generator = generator

    def ask(self, *, user_id: int, question: str) -> AnswerResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Enter a question.")
        if len(question) > 2000:
            raise ValueError("The question is too long.")
        request_id = str(uuid4())
        started = perf_counter()
        sources = self.retriever.retrieve(user_id=user_id, question=question.strip())
        if not sources:
            return AnswerResult(answer=NO_DATA_ANSWER, sources=[])

        prompt = build_prompt(question.strip(), sources)
        try:
            model_response = self.generator.generate(prompt)
        except Exception as error:
            logger.warning(
                "assistant_generation_failed request_id=%s code=%s duration_ms=%d",
                request_id,
                "generation_failed",
                round((perf_counter() - started) * 1000),
            )
            if isinstance(error, GenerationError):
                raise
            raise GenerationError("The local model request failed.") from error

        result = _parse_model_response(model_response, sources)
        logger.info(
            "assistant_generation_completed request_id=%s code=%s duration_ms=%d",
            request_id,
            "ok",
            round((perf_counter() - started) * 1000),
        )
        return result


def _parse_model_response(response: str, sources: list[RetrievedSource]) -> AnswerResult:
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError) as error:
        raise InvalidModelResponseError("The local model returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise InvalidModelResponseError("The local model returned an invalid response.")
    answer = payload.get("answer")
    citations = payload.get("citations")
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or not isinstance(citations, list)
        or not citations
        or any(not isinstance(citation, str) for citation in citations)
    ):
        raise InvalidModelResponseError("The local model returned an incomplete response.")
    source_by_id = {source.citation_id: source for source in sources}
    if len(set(citations)) != len(citations) or any(
        citation not in source_by_id for citation in citations
    ):
        raise InvalidCitationError("The local model returned an unknown citation.")
    answer_sources = [
        AnswerSource(
            citation_id=source.citation_id,
            document_id=source.document_id,
            filename=source.filename,
            page_number=source.page_number,
            url=source.url,
        )
        for source in (source_by_id[citation] for citation in citations)
    ]
    return AnswerResult(answer=answer.strip(), sources=answer_sources)
