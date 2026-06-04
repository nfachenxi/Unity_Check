from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unity_check.db import Base

if TYPE_CHECKING:
    from unity_check.models import EvaluationRound, RuleResult


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
    overall_score: Mapped[float | None] = mapped_column()
    final_risk_level: Mapped[str | None] = mapped_column(String(16))
    recommendation: Mapped[str | None] = mapped_column(String(32))
    executive_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # relationships
    rule_results: Mapped[list["RuleResult"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    evaluation_rounds: Mapped[list["EvaluationRound"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class RuleResult(Base):
    """Rule violations detected by the Roslyn analyzer for a specific event."""

    __tablename__ = "rule_results"
    __table_args__ = (
        Index("idx_rule_results_event_rule", "event_id", "rule_id"),
        Index("idx_rule_results_event_severity", "event_id", "severity"),
        Index("idx_rule_results_event_category", "event_id", "category"),
        Index("idx_rule_results_rule_severity", "rule_id", "severity", "created_at"),
        Index("idx_rule_results_file_path", "file_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("github_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(32), index=True)  # e.g. "CA1822", "SA1200", "RCS1005"
    rule_name: Mapped[str] = mapped_column(String(128))  # e.g. "Member can be marked as static"
    file_path: Mapped[str] = mapped_column(String(1024))
    line_number: Mapped[int | None] = mapped_column()
    column_number: Mapped[int | None] = mapped_column()
    severity: Mapped[str] = mapped_column(String(16), index=True)  # Error / Warning / Info
    category: Mapped[str | None] = mapped_column(String(64), index=True)  # Performance, Naming, etc.
    message: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    scan_type: Mapped[str] = mapped_column(String(16), default="incremental")  # baseline / incremental
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # relationships
    event: Mapped["GithubEvent"] = relationship(back_populates="rule_results")


class EvaluationRound(Base):
    """One round of evaluation within the multi-round pipeline.

    Round 1 (rule_check): Roslyn static-analysis summary — populated from rule_results.
    Round 2 (semantic_review): LLM semantic review — architecture / design / Unity anti-patterns.
    Round 3 (synthesis): LLM synthesis — overall score, risk level, recommendation.
    """

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
    round_number: Mapped[int] = mapped_column()  # 1, 2, 3
    round_type: Mapped[str] = mapped_column(String(32))  # rule_check / semantic_review / synthesis
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


class RepoScanConfig(Base):
    """Per-repository scan configuration stored in DB for multi-repo support."""

    __tablename__ = "repo_scan_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    repository: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    analyze_paths: Mapped[list[str]] = mapped_column(JSON, default=list)  # e.g. ["Assets/Scripts"]
    is_baseline_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline_scan_status: Mapped[str | None] = mapped_column(String(16))  # pending / running / done / failed
    baseline_total_files: Mapped[int | None] = mapped_column()
    baseline_total_issues: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
