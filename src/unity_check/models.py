from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from unity_check.db import Base


class GithubEvent(Base):
    __tablename__ = "github_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    delivery_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str | None] = mapped_column(String(64), index=True)
    repository: Mapped[str | None] = mapped_column(String(255), index=True)
    after_sha: Mapped[str | None] = mapped_column(String(40), index=True)
    before_sha: Mapped[str | None] = mapped_column(String(40))
    clone_path: Mapped[str | None] = mapped_column(String(512))
    diff_content: Mapped[str | None] = mapped_column(Text)
    diff_size: Mapped[int | None] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    evaluation_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
