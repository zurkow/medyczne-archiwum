from __future__ import annotations

import json
from dataclasses import dataclass

import pytest


@dataclass
class FakeSource:
    citation_id: str = "chunk-opaque-1"
    document_id: str = "document-1"
    owner_user_id: int = 7
    filename: str = "wynik.pdf"
    page_number: int = 2
    text: str = "Badanie wykonano 12.10.2026."
    score: float = 0.9
    url: str = "/documents/document-1/preview#page=2"


class FakeRetriever:
    def __init__(self, sources: list[FakeSource]) -> None:
        self.sources = sources

    def retrieve(self, *, user_id: int, question: str) -> list[FakeSource]:
        return self.sources


class FakeGenerator:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0
        self.citation_ids: list[str] = []

    def generate(self, prompt: str, *, citation_ids) -> str:
        self.calls += 1
        self.citation_ids = list(citation_ids)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _service(retriever: FakeRetriever, generator: FakeGenerator):
    from medical_archive.rag.service import AnswerService

    return AnswerService(retriever=retriever, generator=generator)


def test_answer_returns_only_app_validated_clickable_source_metadata():
    source = FakeSource()
    generator = FakeGenerator(
        json.dumps(
            {
                "answer": "Badanie wykonano 12 października 2026 roku.",
                "citations": [source.citation_id],
            },
            ensure_ascii=False,
        )
    )

    result = _service(FakeRetriever([source]), generator).ask(
        user_id=source.owner_user_id,
        question="Kiedy wykonano badanie?",
    )

    assert result.answer == "Badanie wykonano 12 października 2026 roku."
    assert len(result.sources) == 1
    assert result.sources[0].filename == "wynik.pdf"
    assert result.sources[0].url == "/documents/document-1/preview#page=2"
    assert generator.calls == 1


def test_no_relevant_source_returns_explicit_no_data_without_calling_generator():
    generator = FakeGenerator('{"answer":"zgaduję","citations":[]}')

    result = _service(FakeRetriever([]), generator).ask(
        user_id=7,
        question="Jaka jest moja diagnoza?",
    )

    assert (
        result.answer
        == "Nie znalazłem w dostępnych źródłach informacji, która odpowiada na to pytanie."
    )
    assert result.sources == []
    assert generator.calls == 0


def test_answer_rejects_unknown_model_citation_ids():
    generator = FakeGenerator('{"answer":"Odpowiedź bez podstaw.","citations":["forged-id"]}')

    with pytest.raises(Exception, match="citation"):
        _service(FakeRetriever([FakeSource()]), generator).ask(
            user_id=7,
            question="Co mówi dokument?",
        )


@pytest.mark.parametrize(
    "response",
    ["not-json", '{"answer":"Odpowiedź","citations":[]}'],
    ids=["malformed-json", "missing-citation"],
)
def test_answer_rejects_malformed_or_uncited_model_output(response: str):
    generator = FakeGenerator(response)

    with pytest.raises(Exception, match="(?i)invalid JSON|citation|incomplete"):
        _service(FakeRetriever([FakeSource()]), generator).ask(
            user_id=7,
            question="Co mówi dokument?",
        )


def test_generation_failure_is_reported_without_a_fallback_answer():
    generator = FakeGenerator(TimeoutError("local model timed out"))

    with pytest.raises(Exception, match="generation|model"):
        _service(FakeRetriever([FakeSource()]), generator).ask(
            user_id=7,
            question="Co mówi dokument?",
        )


def test_lm_studio_uses_json_schema_response_mode():
    from medical_archive.rag.service import LMStudioClient

    captured = {}

    def request(endpoint, payload, timeout):
        captured.update(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"answer":"ok","citations":["source-1"]}',
                    }
                }
            ]
        }

    client = LMStudioClient(
        base_url="http://127.0.0.1:1234/v1",
        model="bielik-1.5b-v3.0-instruct",
        request=request,
    )
    client.generate("test prompt", citation_ids=["source-1"])

    assert captured["response_format"]["type"] == "json_schema"


def test_lm_studio_schema_limits_citations_to_retrieved_source_ids():
    from medical_archive.rag.service import LMStudioClient

    captured = {}

    def request(endpoint, payload, timeout):
        captured.update(payload)
        return {"choices": [{"message": {"content": '{"answer":"ok","citations":["chunk-1"]}'}}]}

    client = LMStudioClient(
        base_url="http://127.0.0.1:1234/v1",
        model="bielik-1.5b-v3.0-instruct",
        request=request,
    )
    client.generate("test prompt", citation_ids=["chunk-1", "measurement:abc"])

    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["citations"]["items"]["enum"] == ["chunk-1", "measurement:abc"]


def test_answer_service_offers_only_retrieved_source_ids_to_the_model():
    source = FakeSource()
    generator = FakeGenerator(
        json.dumps({"answer": "Badanie wykonano.", "citations": [source.citation_id]})
    )

    _service(FakeRetriever([source]), generator).ask(user_id=7, question="Kiedy badanie?")

    assert generator.citation_ids == [source.citation_id]
