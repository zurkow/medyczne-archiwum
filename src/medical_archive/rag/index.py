import json
import sqlite3
from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from medical_archive.documents.extraction import ExtractedPage
from medical_archive.documents.models import Document
from medical_archive.models import AccessGrant
from medical_archive.rag.chunking import chunk_pages
from medical_archive.rag.embeddings import validate_embeddings
from medical_archive.rag.models import EMBEDDING_DIMENSIONS, ChunkMetadata


class VectorExtensionUnavailableError(RuntimeError):
    """Raised when sqlite-vec cannot be loaded on a SQLite connection."""


class IndexingError(RuntimeError):
    """Raised when document chunks cannot be committed to the vector index."""


def load_sqlite_vec(connection: Connection | sqlite3.Connection) -> None:
    raw_connection = connection
    if isinstance(connection, Connection):
        raw_connection = connection.connection.driver_connection

    try:
        raw_connection.execute("SELECT vec_version()").fetchone()
        return
    except sqlite3.Error:
        pass

    try:
        import sqlite_vec

        raw_connection.enable_load_extension(True)
        try:
            sqlite_vec.load(raw_connection)
        finally:
            raw_connection.enable_load_extension(False)
        raw_connection.execute("SELECT vec_version()").fetchone()
    except (ImportError, AttributeError, OSError, sqlite3.Error) as error:
        raise VectorExtensionUnavailableError(
            "sqlite-vec is required for vector indexing. Install the local-ai dependencies."
        ) from error


class DocumentVectorIndexer:
    def __init__(self, session: Session, *, embedder) -> None:
        dimensions = getattr(embedder, "dimensions", None)
        if dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"The configured embedding model must return {EMBEDDING_DIMENSIONS} dimensions."
            )
        self.session = session
        self.embedder = embedder

    def index_document(self, document: Document, pages: Sequence[ExtractedPage]) -> None:
        chunks = chunk_pages(pages)
        if not chunks:
            raise IndexingError("The document does not contain indexable text.")

        try:
            vectors = validate_embeddings(
                self.embedder.embed([chunk.text for chunk in chunks]),
                expected_count=len(chunks),
                dimensions=EMBEDDING_DIMENSIONS,
            )
        except Exception as error:
            raise IndexingError("The document could not be embedded.") from error

        try:
            load_sqlite_vec(self.session.connection())
            grants = self.session.scalars(
                select(AccessGrant.grantee_user_id).where(
                    AccessGrant.owner_user_id == document.owner_user_id,
                    AccessGrant.resource_type == "document",
                    AccessGrant.resource_id == document.id,
                )
            ).all()
            partitions = {str(document.owner_user_id), *(str(grantee) for grantee in grants)}
            self._replace_document_index(document, chunks, vectors, partitions)
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if isinstance(error, IndexingError):
                raise
            raise IndexingError("The document index could not be committed.") from error

    def apply_grant(self, document: Document, grant: AccessGrant) -> None:
        if (
            grant.resource_type != "document"
            or grant.resource_id != document.id
            or grant.owner_user_id != document.owner_user_id
            or document.processing_status != "ready"
        ):
            raise IndexingError("Only an approved document grant can be indexed.")
        try:
            load_sqlite_vec(self.session.connection())
            source_chunks = self.session.scalars(
                select(ChunkMetadata).where(
                    ChunkMetadata.document_id == document.id,
                    ChunkMetadata.owner_partition == str(document.owner_user_id),
                )
            ).all()
            if not source_chunks:
                if not document.pages:
                    return
                raise IndexingError("The approved document has no owner index to share.")
            connection = self.session.connection()
            for source in source_chunks:
                vector = connection.exec_driver_sql(
                    "SELECT embedding FROM rag_chunk_vectors "
                    "WHERE chunk_id = ? AND owner_partition = ?",
                    (source.chunk_id, source.owner_partition),
                ).scalar_one()
                self._insert_chunk(
                    connection,
                    chunk_id=str(uuid4()),
                    owner_partition=str(grant.grantee_user_id),
                    document_id=document.id,
                    page_number=source.page_number,
                    vector=vector,
                )
            self.session.flush()
        except Exception as error:
            if isinstance(error, IndexingError):
                raise
            raise IndexingError("The document index could not be shared.") from error

    def remove_from_partition(self, *, owner_id: int, document_id: str) -> None:
        try:
            load_sqlite_vec(self.session.connection())
            chunks = self.session.scalars(
                select(ChunkMetadata).where(
                    ChunkMetadata.document_id == document_id,
                    ChunkMetadata.owner_partition == str(owner_id),
                )
            ).all()
            connection = self.session.connection()
            for chunk in chunks:
                connection.exec_driver_sql(
                    "DELETE FROM rag_chunk_vectors WHERE chunk_id = ?",
                    (chunk.chunk_id,),
                )
            self.session.execute(
                delete(ChunkMetadata).where(
                    ChunkMetadata.document_id == document_id,
                    ChunkMetadata.owner_partition == str(owner_id),
                )
            )
            self.session.flush()
        except Exception as error:
            raise IndexingError("The recipient's document index could not be removed.") from error

    def _replace_document_index(
        self,
        document: Document,
        chunks,
        vectors: Sequence[Sequence[float]],
        partitions: set[str],
    ) -> None:
        old_chunks = self.session.scalars(
            select(ChunkMetadata).where(ChunkMetadata.document_id == document.id)
        ).all()
        connection = self.session.connection()
        for partition in partitions:
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._insert_chunk(
                    connection,
                    chunk_id=str(uuid4()),
                    owner_partition=partition,
                    document_id=document.id,
                    page_number=chunk.page_number,
                    vector=json.dumps(vector, separators=(",", ":")),
                )

        self.session.flush()
        for chunk in old_chunks:
            connection.exec_driver_sql(
                "DELETE FROM rag_chunk_vectors WHERE chunk_id = ?",
                (chunk.chunk_id,),
            )
        if old_chunks:
            self.session.execute(
                delete(ChunkMetadata).where(
                    ChunkMetadata.chunk_id.in_([chunk.chunk_id for chunk in old_chunks])
                )
            )

    def _insert_chunk(
        self,
        connection: Connection,
        *,
        chunk_id: str,
        owner_partition: str,
        document_id: str,
        page_number: int,
        vector,
    ) -> None:
        connection.exec_driver_sql(
            "INSERT INTO rag_chunk_vectors (chunk_id, owner_partition, embedding) VALUES (?, ?, ?)",
            (chunk_id, owner_partition, vector),
        )
        self.session.add(
            ChunkMetadata(
                chunk_id=chunk_id,
                owner_partition=owner_partition,
                document_id=document_id,
                page_number=page_number,
            )
        )
