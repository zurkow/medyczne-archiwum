import json
import math
from collections.abc import Callable, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class EmbeddingError(RuntimeError):
    """Raised when the local embedding service fails or returns invalid vectors."""


EmbeddingRequest = Callable[[str, Sequence[str]], Sequence[Sequence[float]]]


def validate_embeddings(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    dimensions: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError("The embedding service returned an unexpected vector count.")
    validated: list[list[float]] = []
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError("The embedding service returned an invalid vector dimension.")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError, OverflowError) as error:
            raise EmbeddingError("The embedding service returned an invalid vector.") from error
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingError("The embedding service returned a non-finite vector.")
        validated.append(values)
    return validated


class EmbeddingClient:
    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        base_url: str = "http://127.0.0.1:1234/v1",
        timeout: float = 15.0,
        request: EmbeddingRequest | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("An embedding model name must be configured.")
        if dimensions < 1:
            raise ValueError("Embedding dimensions must be positive.")
        parsed_url = urlsplit(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("The embedding service must use a loopback LM Studio URL.")
        self.model = model
        self.dimensions = dimensions
        self.endpoint = f"{base_url.rstrip('/')}/embeddings"
        self.timeout = timeout
        self._request = request or self._request_local_service

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text for text in texts):
            raise EmbeddingError("Embedding input must contain non-empty text strings.")
        try:
            vectors = self._request(self.model, texts)
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError("The local embedding service could not be reached.") from error
        return validate_embeddings(
            vectors,
            expected_count=len(texts),
            dimensions=self.dimensions,
        )

    def _request_local_service(self, model: str, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        payload = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as error:
            raise EmbeddingError("The local embedding service request failed.") from error

        try:
            items = result["data"]
            if not isinstance(items, list):
                raise TypeError
            if all(isinstance(item.get("index"), int) for item in items):
                items = sorted(items, key=lambda item: item["index"])
            return [item["embedding"] for item in items]
        except (KeyError, TypeError, AttributeError) as error:
            raise EmbeddingError(
                "The local embedding service returned an invalid response."
            ) from error
