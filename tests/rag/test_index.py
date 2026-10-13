from __future__ import annotations

import sqlite3
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from medical_archive.documents.extraction import ExtractedPage
from medical_archive.documents.models import Document, DocumentPage
from medical_archive.models import AccessGrant, User


class FakeEmbeddingClient:
    dimensions = 768

    def __init__(self) -> None:
        self.fail = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding service unavailable")
        return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]


@pytest.fixture
def indexed_document(auth_db: Session, member_user: User) -> Document:
    document = Document(
        id=str(uuid4()),
        owner_user_id=member_user.id,
        storage_key=str(uuid4()),
        original_filename="result.pdf",
        mime_type="application/pdf",
        byte_size=1,
        processing_status="ready",
    )
    document.pages.append(
        DocumentPage(
            page_number=1,
            text="Badanie krwi. Wynik prawidłowy.",
            extraction_method="text",
        )
    )
    document.pages.append(
        DocumentPage(
            page_number=2,
            text="Wartość referencyjna.",
            extraction_method="text",
        )
    )
    auth_db.add(document)
    auth_db.commit()
    return document


def _indexer(session: Session, embeddings: FakeEmbeddingClient):
    module = _module("medical_archive.rag.index")
    return module.DocumentVectorIndexer(session, embedder=embeddings)


def _module(name: str):
    try:
        return import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"The RAG module {name!r} has not been implemented.")


def _documents_in_partition(
    session: Session, *, owner_id: int, query_vector: list[float]
) -> list[str]:
    rows = session.execute(
        text(
            "SELECT metadata.document_id "
            "FROM rag_chunk_vectors AS vectors "
            "JOIN rag_chunk_metadata AS metadata ON metadata.chunk_id = vectors.chunk_id "
            "WHERE vectors.embedding MATCH :query "
            "AND k = :limit "
            "AND vectors.owner_partition = :partition"
        ),
        {
            "query": str(query_vector),
            "limit": 20,
            "partition": str(owner_id),
        },
    )
    return [row[0] for row in rows]


def _query_vector() -> list[float]:
    return [1.0, *([0.0] * 767)]


def test_indexed_chunks_are_private_until_explicit_grant_and_removed_on_revoke(
    auth_db: Session,
    indexed_document: Document,
    member_user: User,
    admin_user: User,
):
    embeddings = FakeEmbeddingClient()
    indexer = _indexer(auth_db, embeddings)
    indexer.index_document(indexed_document, list(indexed_document.pages))

    assert sorted(
        _documents_in_partition(auth_db, owner_id=member_user.id, query_vector=_query_vector())
    ) == [indexed_document.id, indexed_document.id]
    assert (
        _documents_in_partition(auth_db, owner_id=admin_user.id, query_vector=_query_vector()) == []
    )
    assert auth_db.query(AccessGrant).count() == 0
    metadata_model = _module("medical_archive.rag.models").ChunkMetadata
    metadata = auth_db.scalars(
        select(metadata_model).where(metadata_model.owner_partition == str(member_user.id))
    ).all()
    assert {record.page_number for record in metadata} == {1, 2}
    assert all(record.document_id == indexed_document.id for record in metadata)
    for record in metadata:
        assert str(UUID(record.chunk_id)) == record.chunk_id

    recipient = User(
        email="rag-recipient@example.test",
        password_hash="not-used-in-this-test",
        is_admin=False,
    )
    auth_db.add(recipient)
    auth_db.commit()
    grant = _module("medical_archive.auth.grants").create(
        auth_db,
        owner_user_id=member_user.id,
        grantee_user_id=recipient.id,
        resource_type="document",
        resource_id=indexed_document.id,
        verify_owner=lambda resource_id, owner_id: (
            resource_id == indexed_document.id and owner_id == member_user.id
        ),
        on_grant=lambda active_grant: indexer.apply_grant(indexed_document, active_grant),
    )

    assert _documents_in_partition(
        auth_db, owner_id=recipient.id, query_vector=_query_vector()
    ) == [indexed_document.id, indexed_document.id]
    assert (
        _documents_in_partition(auth_db, owner_id=admin_user.id, query_vector=_query_vector()) == []
    )

    _module("medical_archive.auth.grants").revoke(
        auth_db,
        grant_id=grant.id,
        owner_user_id=member_user.id,
        on_revoke=lambda active_grant: indexer.remove_from_partition(
            owner_id=active_grant.grantee_user_id,
            document_id=active_grant.resource_id,
        ),
    )

    assert (
        _documents_in_partition(auth_db, owner_id=recipient.id, query_vector=_query_vector()) == []
    )


def test_chunks_overlap_within_a_page_and_never_cross_page_boundaries():
    chunking = _module("medical_archive.rag.chunking")
    chunks = chunking.chunk_pages(
        [
            ExtractedPage(1, "a b c d", "text"),
            ExtractedPage(2, "e f", "text"),
        ],
        max_tokens=3,
        overlap_tokens=1,
    )

    assert [(chunk.page_number, chunk.text) for chunk in chunks] == [
        (1, "a b c"),
        (1, "c d"),
        (2, "e f"),
    ]


def test_empty_ready_document_grant_has_no_vector_rows_to_replicate(
    auth_db: Session,
    member_user: User,
    admin_user: User,
):
    document = Document(
        id=str(uuid4()),
        owner_user_id=member_user.id,
        storage_key=str(uuid4()),
        original_filename="empty.pdf",
        mime_type="application/pdf",
        byte_size=1,
        processing_status="ready",
    )
    auth_db.add(document)
    auth_db.commit()
    grant = AccessGrant(
        owner_user_id=member_user.id,
        grantee_user_id=admin_user.id,
        resource_type="document",
        resource_id=document.id,
    )
    indexer = _indexer(auth_db, FakeEmbeddingClient())

    indexer.apply_grant(document, grant)

    metadata_model = _module("medical_archive.rag.models").ChunkMetadata
    assert auth_db.scalars(select(metadata_model)).all() == []


def test_earlier_sqlite_migrations_do_not_require_sqlite_vec(tmp_path: Path, monkeypatch):
    database = tmp_path / "without-vector-extension.db"
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    monkeypatch.delenv("MEDICAL_ARCHIVE_DATABASE_URL", raising=False)
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)

    command.upgrade(config, "0003_document_text")

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0003_document_text",)
        ]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"documents", "document_pages"} <= tables
        assert "rag_chunk_vectors" not in tables
    finally:
        connection.close()


@pytest.mark.parametrize(
    "embeddings",
    [
        [[1.0, 0.0, 0.0]],
        [[1.0, 0.0], [1.0, 0.0]],
        [[1.0, float("nan"), 0.0], [1.0, 0.0, 0.0]],
        [[1.0, float("inf"), 0.0], [1.0, 0.0, 0.0]],
    ],
    ids=["wrong-count", "wrong-dimension", "nan-value", "infinite-value"],
)
def test_embedding_client_rejects_invalid_vectors_before_indexing(embeddings):
    embedding_module = _module("medical_archive.rag.embeddings")
    client = embedding_module.EmbeddingClient(
        model="configured-local-embedding-model",
        dimensions=3,
        request=lambda _model, _texts: embeddings,
    )

    with pytest.raises(embedding_module.EmbeddingError):
        client.embed(["first", "second"])


def test_embedding_client_rejects_non_loopback_service_urls():
    embedding_module = _module("medical_archive.rag.embeddings")

    with pytest.raises(ValueError, match="loopback"):
        embedding_module.EmbeddingClient(
            model="configured-local-embedding-model",
            dimensions=3,
            base_url="http://192.0.2.10:1234/v1",
        )


def test_failed_reindex_keeps_the_previous_committed_index(
    auth_db: Session,
    indexed_document: Document,
    member_user: User,
):
    embeddings = FakeEmbeddingClient()
    indexer = _indexer(auth_db, embeddings)
    indexer.index_document(indexed_document, list(indexed_document.pages))
    failure = SimpleNamespace(remaining=True)

    def fail_next_commit(_session: Session) -> None:
        if failure.remaining:
            failure.remaining = False
            raise RuntimeError("simulated database commit failure")

    event.listen(auth_db, "before_commit", fail_next_commit)
    try:
        with pytest.raises(_module("medical_archive.rag.index").IndexingError):
            indexer.index_document(
                indexed_document,
                [ExtractedPage(1, "Replacement content.", "text")],
            )
    finally:
        event.remove(auth_db, "before_commit", fail_next_commit)
        auth_db.rollback()

    assert sorted(
        _documents_in_partition(auth_db, owner_id=member_user.id, query_vector=_query_vector())
    ) == [indexed_document.id, indexed_document.id]
