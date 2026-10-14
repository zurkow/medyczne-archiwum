import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.documents.models import Document, DocumentPage
from medical_archive.measurements.models import Measurement
from medical_archive.rag.embeddings import EmbeddingError
from medical_archive.rag.models import ChunkMetadata

MAX_MEASUREMENT_SOURCES = 8
MAX_MEASUREMENT_CANDIDATES = 100
MEASUREMENT_RECENT_DAYS = 365
_GENERIC_MEASUREMENT_QUESTION_TERMS = {
    "pomiar",
    "pomiary",
    "pomiarow",
    "wartosc",
    "wartosci",
    "wynik",
    "wyniki",
}
_QUESTION_STOP_WORDS = {
    "jak",
    "jaka",
    "jakie",
    "jakich",
    "jaki",
    "moje",
    "moich",
    "moj",
    "moja",
    "swoje",
    "swoj",
    "zapisalem",
    "zapisalam",
    "zapisano",
    "zapisany",
    "zapisana",
    "zapisane",
    "odczyt",
    "odczyty",
    "dnia",
    "dniu",
}


class RetrievalError(RuntimeError):
    """Raised when authorized document sources cannot be searched safely."""


@dataclass(frozen=True)
class RetrievedSource:
    citation_id: str
    document_id: str
    owner_user_id: int
    filename: str
    page_number: int
    text: str
    score: float
    url: str
    kind: Literal["document"] = "document"


@dataclass(frozen=True)
class RetrievedMeasurementSource:
    citation_id: str
    measurement_id: str
    owner_user_id: int
    name: str
    value: str
    unit: str
    measured_at: datetime
    note: str | None
    url: str
    kind: Literal["measurement"] = "measurement"


class DocumentRetriever:
    def __init__(
        self,
        session: Session,
        *,
        embedder,
        relevance_threshold: float = 0.2,
        top_k: int = 8,
    ) -> None:
        if not 0.0 <= relevance_threshold <= 1.0:
            raise ValueError("The retrieval relevance threshold must be between 0 and 1.")
        if top_k < 1:
            raise ValueError("The retrieval result limit must be positive.")
        self.session = session
        self.embedder = embedder
        self.relevance_threshold = relevance_threshold
        self.top_k = top_k

    def retrieve(
        self,
        *,
        user_id: int,
        question: str,
    ) -> list[RetrievedSource | RetrievedMeasurementSource]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Enter a question.")

        measurement_sources = self._retrieve_measurements(
            user_id=user_id,
            question=question.strip(),
        )
        owned_ids = self.session.scalars(
            select(Document.id).where(
                Document.owner_user_id == user_id,
                Document.processing_status == "ready",
            )
        ).all()
        visible_ids = visible_resource_ids(
            self.session,
            user_id=user_id,
            resource_type="document",
            owned_resource_ids=owned_ids,
        )
        if not visible_ids:
            return measurement_sources

        try:
            vectors = self.embedder.embed([question.strip()])
            if len(vectors) != 1:
                raise RetrievalError("The local embedding service returned an invalid result.")
            query_vector = json.dumps(vectors[0], separators=(",", ":"))
            rows = self.session.execute(
                text(
                    "SELECT chunk_id, distance FROM rag_chunk_vectors "
                    "WHERE embedding MATCH :embedding "
                    "AND k = :limit "
                    "AND owner_partition = :partition"
                ),
                {
                    "embedding": query_vector,
                    "limit": self.top_k,
                    "partition": str(user_id),
                },
            ).all()
        except (EmbeddingError, OSError, TimeoutError) as error:
            raise RetrievalError("The local search service is unavailable.") from error
        except Exception as error:
            if isinstance(error, RetrievalError):
                raise
            raise RetrievalError("The document search could not be completed.") from error

        if not rows:
            return measurement_sources

        distances = {chunk_id: float(distance) for chunk_id, distance in rows}
        metadata = self.session.scalars(
            select(ChunkMetadata).where(
                ChunkMetadata.chunk_id.in_(distances),
                ChunkMetadata.owner_partition == str(user_id),
            )
        ).all()
        documents = self.session.scalars(
            select(Document).where(
                Document.id.in_(visible_ids),
                Document.id.in_({record.document_id for record in metadata}),
                Document.processing_status == "ready",
            )
        ).all()
        documents_by_id = {
            document.id: document
            for document in documents
            if can_access(
                self.session,
                user_id=user_id,
                resource_type="document",
                resource_id=document.id,
                owner_user_id=document.owner_user_id,
            )
        }
        if not documents_by_id:
            return measurement_sources

        page_keys = {
            (record.document_id, record.page_number)
            for record in metadata
            if record.document_id in documents_by_id
            and 1.0 - distances[record.chunk_id] >= self.relevance_threshold
        }
        pages = self.session.scalars(
            select(DocumentPage).where(
                DocumentPage.document_id.in_({document_id for document_id, _ in page_keys}),
                DocumentPage.page_number.in_({page_number for _, page_number in page_keys}),
            )
        ).all()
        pages_by_key = {(page.document_id, page.page_number): page for page in pages}

        sources: list[RetrievedSource] = []
        seen_pages: set[tuple[str, int]] = set()
        for record in sorted(metadata, key=lambda item: distances[item.chunk_id]):
            document = documents_by_id.get(record.document_id)
            score = 1.0 - distances[record.chunk_id]
            page_key = (record.document_id, record.page_number)
            page = pages_by_key.get(page_key)
            if (
                document is None
                or score < self.relevance_threshold
                or page is None
                or not page.text.strip()
                or page_key in seen_pages
            ):
                continue
            seen_pages.add(page_key)
            sources.append(
                RetrievedSource(
                    citation_id=record.chunk_id,
                    document_id=document.id,
                    owner_user_id=document.owner_user_id,
                    filename=document.original_filename,
                    page_number=page.page_number,
                    text=page.text,
                    score=score,
                    url=f"/documents/{document.id}/preview#page={page.page_number}",
                )
            )
        return [*sources, *measurement_sources]

    def _retrieve_measurements(
        self,
        *,
        user_id: int,
        question: str,
    ) -> list[RetrievedMeasurementSource]:
        owned_ids = self.session.scalars(
            select(Measurement.id).where(Measurement.owner_user_id == user_id)
        ).all()
        visible_ids = visible_resource_ids(
            self.session,
            user_id=user_id,
            resource_type="measurement",
            owned_resource_ids=owned_ids,
        )
        if not visible_ids:
            return []

        now = datetime.now(UTC)
        candidates = self.session.scalars(
            select(Measurement)
            .where(
                Measurement.id.in_(visible_ids),
                Measurement.measured_at_utc >= now - timedelta(days=MEASUREMENT_RECENT_DAYS),
                Measurement.measured_at_utc <= now,
            )
            .order_by(Measurement.measured_at_utc.desc(), Measurement.id)
            .limit(MAX_MEASUREMENT_CANDIDATES)
        ).all()
        matching = [
            measurement
            for measurement in candidates
            if (
                measurement.owner_user_id == user_id
                or can_access(
                    self.session,
                    user_id=user_id,
                    resource_type="measurement",
                    resource_id=measurement.id,
                    owner_user_id=measurement.owner_user_id,
                )
            )
            and _measurement_matches_question(measurement.name, question)
        ]
        sources: list[RetrievedMeasurementSource] = []
        for measurement in matching[:MAX_MEASUREMENT_SOURCES]:
            measured_at = measurement.measured_at_utc
            if measured_at.tzinfo is None:
                measured_at = measured_at.replace(tzinfo=UTC)
            else:
                measured_at = measured_at.astimezone(UTC)
            sources.append(
                RetrievedMeasurementSource(
                    citation_id=f"measurement:{measurement.id}",
                    measurement_id=measurement.id,
                    owner_user_id=measurement.owner_user_id,
                    name=measurement.name,
                    value=str(measurement.value),
                    unit=measurement.unit,
                    measured_at=measured_at,
                    note=measurement.note[:1000] if measurement.note is not None else None,
                    url=f"/measurements/{measurement.id}",
                )
            )
        return sources


def _measurement_matches_question(name: str, question: str) -> bool:
    question_terms = {
        term
        for term in _normalize_terms(question)
        if term not in _QUESTION_STOP_WORDS and len(term) > 2
    }
    if question_terms & _GENERIC_MEASUREMENT_QUESTION_TERMS:
        return True
    return bool(question_terms & set(_normalize_terms(name)))


def _normalize_terms(value: str) -> list[str]:
    normalized = value.casefold().translate(
        str.maketrans(
            {
                "ą": "a",
                "ć": "c",
                "ę": "e",
                "ł": "l",
                "ń": "n",
                "ó": "o",
                "ś": "s",
                "ź": "z",
                "ż": "z",
            }
        )
    )
    return re.findall(r"[^\W\d_]+", normalized, flags=re.UNICODE)
