from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
