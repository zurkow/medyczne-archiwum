from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from medical_archive.documents.models import Document, DocumentPage
from medical_archive.models import AccessGrant, User


class FakeEmbeddingClient:
    dimensions = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]


def _module(name: str):
    from importlib import import_module

    try:
        return import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"The RAG module {name!r} has not been implemented.")


def _document(owner: User, text: str, *, status: str = "ready") -> Document:
    document = Document(
        id=str(uuid4()),
        owner_user_id=owner.id,
        storage_key=str(uuid4()),
        original_filename="wynik badania.pdf",
        mime_type="application/pdf",
        byte_size=100,
        processing_status=status,
    )
    document.pages.append(DocumentPage(page_number=1, text=text, extraction_method="text"))
    return document


def _retriever(session: Session):
    module = _module("medical_archive.rag.retrieval")
    return module.DocumentRetriever(
        session,
        embedder=FakeEmbeddingClient(),
        relevance_threshold=0.2,
        top_k=10,
    )


def test_retrieval_returns_owned_and_explicitly_shared_ready_sources(
    auth_db: Session,
    member_user: User,
    admin_user: User,
):
    owned = _document(member_user, "Morfologia krwi z dnia 12.10.2026.")
    shared = _document(admin_user, "Wynik badania glukozy z dnia 10.10.2026.")
    pending = _document(member_user, "Nie zatwierdzony dokument.", status="needs_review")
    auth_db.add_all([owned, shared, pending])
    auth_db.commit()

    indexer = _module("medical_archive.rag.index").DocumentVectorIndexer(
        auth_db, embedder=FakeEmbeddingClient()
    )
    indexer.index_document(owned, list(owned.pages))
    indexer.index_document(shared, list(shared.pages))
    grant = AccessGrant(
        owner_user_id=admin_user.id,
        grantee_user_id=member_user.id,
        resource_type="document",
        resource_id=shared.id,
    )
    auth_db.add(grant)
    auth_db.flush()
    indexer.apply_grant(shared, grant)
    indexer.index_document(pending, list(pending.pages))

    sources = _retriever(auth_db).retrieve(
        user_id=member_user.id,
        question="Kiedy wykonano badanie?",
    )

    assert {source.document_id for source in sources} == {owned.id, shared.id}
    assert all(source.owner_user_id in {member_user.id, admin_user.id} for source in sources)
    assert all("Nie zatwierdzony" not in source.text for source in sources)


def test_retrieval_rechecks_current_grants_before_returning_stale_partition_chunks(
    auth_db: Session,
    member_user: User,
    admin_user: User,
):
    document = _document(admin_user, "Prywatny wynik badania.")
    auth_db.add(document)
    auth_db.commit()
    indexer = _module("medical_archive.rag.index").DocumentVectorIndexer(
        auth_db, embedder=FakeEmbeddingClient()
    )
    indexer.index_document(document, list(document.pages))
    grant = AccessGrant(
        owner_user_id=admin_user.id,
        grantee_user_id=member_user.id,
        resource_type="document",
        resource_id=document.id,
    )
    auth_db.add(grant)
    auth_db.flush()
    indexer.apply_grant(document, grant)
    auth_db.commit()
    auth_db.execute(delete(AccessGrant).where(AccessGrant.id == grant.id))
    auth_db.commit()

    sources = _retriever(auth_db).retrieve(
        user_id=member_user.id,
        question="Co zawiera dokument?",
    )

    assert sources == []
