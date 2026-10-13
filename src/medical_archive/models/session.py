from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from medical_archive.db import Base


class LoginSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL AND purpose IN ('login', 'registration')) "
            "OR (user_id IS NOT NULL AND purpose = 'authenticated')",
            name="ck_sessions_identity_purpose",
        ),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
