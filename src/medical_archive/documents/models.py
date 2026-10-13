from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from medical_archive.db import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_documents_byte_size_positive"),
        CheckConstraint(
            "processing_status IN ('needs_review', 'ready', 'ocr_failed', "
            "'index_failed', 'indexing')",
            name="ck_documents_processing_status",
        ),
        Index("ix_documents_owner_user_id", "owner_user_id"),
        Index("ix_documents_processing_status", "processing_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100))
    document_date: Mapped[date | None] = mapped_column(Date)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="needs_review", server_default="needs_review"
    )
    ocr_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
        CheckConstraint("page_number > 0", name="ck_document_pages_page_number_positive"),
        CheckConstraint(
            "extraction_method IN ('text', 'ocr')",
            name="ck_document_pages_extraction_method",
        ),
        Index("ix_document_pages_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)
    document: Mapped[Document] = relationship(back_populates="pages")
