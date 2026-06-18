from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unity_check.db import Base

if TYPE_CHECKING:
    pass


class Repository(Base):
    """Registered git repository for monitoring."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    alias: Mapped[str | None] = mapped_column(String(255))
    clone_url: Mapped[str | None] = mapped_column(String(1024))
    webhook_secret: Mapped[str | None] = mapped_column(String(128))
    ssh_key_path: Mapped[str | None] = mapped_column(String(512))
    branch_filter: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_path: Mapped[str | None] = mapped_column(String(512))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # relationships
    events: Mapped[list["GithubEvent"]] = relationship(back_populates="repository_rel")


class GithubEvent(Base):
    __tablename__ = "github_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    delivery_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str | None] = mapped_column(String(64), index=True)
    repository: Mapped[str | None] = mapped_column(String(255), index=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), index=True
    )
    after_sha: Mapped[str | None] = mapped_column(String(40), index=True)
    before_sha: Mapped[str | None] = mapped_column(String(40))
    clone_path: Mapped[str | None] = mapped_column(String(512))
    diff_content: Mapped[str | None] = mapped_column(Text)
    diff_size: Mapped[int | None] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    overall_score: Mapped[float | None] = mapped_column()
    final_risk_level: Mapped[str | None] = mapped_column(String(16))
    recommendation: Mapped[str | None] = mapped_column(String(32))
    executive_summary: Mapped[str | None] = mapped_column(Text)
    dimension_a_score: Mapped[float | None] = mapped_column()
    dimension_b_score: Mapped[float | None] = mapped_column()
    dimension_a_summary: Mapped[str | None] = mapped_column(Text)
    dimension_b_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # relationships
    evaluation_rounds: Mapped[list["EvaluationRound"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    repository_rel: Mapped["Repository | None"] = relationship(back_populates="events")


class EvaluationRound(Base):
    """Per-file evaluation round: rule_check summary + two LLM dimensions per file."""

    __tablename__ = "evaluation_rounds"
    __table_args__ = (
        Index("idx_eval_rounds_event_round", "event_id", "round_number"),
        Index("idx_eval_rounds_event_type", "event_id", "round_type"),
        Index("idx_eval_rounds_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("github_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    round_number: Mapped[int] = mapped_column()  # file index (0=rule_check, 1..N=file)
    round_type: Mapped[str] = mapped_column(String(32))  # rule_check / functionality_best_practices / security_performance_health
    file_path: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/success/failed/skipped
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # summary of inputs fed to this round
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # structured output from this round
    score: Mapped[float | None] = mapped_column()  # overall score (R3)
    model_name: Mapped[str | None] = mapped_column(String(64))  # LLM model used
    tokens_used: Mapped[int | None] = mapped_column()  # prompt + completion tokens
    duration_ms: Mapped[int | None] = mapped_column()  # wall-clock duration
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # relationships
    event: Mapped["GithubEvent"] = relationship(back_populates="evaluation_rounds")


class Task(Base):
    """Background task queue: scan/evaluation jobs processed by the worker thread."""

    __tablename__ = "tasks"
    __table_args__ = (Index("idx_tasks_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32))  # full_scan, incremental_scan
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending, processing, completed, failed
    progress_detail: Mapped[str | None] = mapped_column(String(256))
    repository_id: Mapped[int | None] = mapped_column(index=True)
    event_id: Mapped[int | None] = mapped_column(index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
