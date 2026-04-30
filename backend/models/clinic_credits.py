"""
backend/models/clinic_credits.py — ClinicCredits + CreditTransaction models.

Per-clinic credit balance in INR (₹).
Each voice call deducts credits based on duration × per-minute rate.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from backend.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClinicCredits(Base):
    """Per-tenant credit balance — one row per clinic."""
    __tablename__ = "clinic_credits"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Balance in INR (₹) — float is fine for billing granularity
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Per-minute call rate in ₹ (default ₹1.50/min)
    rate_per_minute: Mapped[float] = mapped_column(Float, nullable=False, default=1.50)

    # Total credits ever added / deducted (running aggregates)
    total_added: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_deducted: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Low balance alert threshold (₹)
    low_balance_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<ClinicCredits tenant={self.tenant_id} balance=₹{self.balance:.2f}>"


class CreditTransaction(Base):
    """Immutable ledger of every credit change (add / deduct)."""
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # "topup" | "call_deduction" | "adjustment" | "refund"
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Positive = credit added, Negative = credit deducted
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    # Balance AFTER this transaction
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)

    # Description (e.g. "Call 3m 22s @ ₹1.50/min" or "Admin top-up")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional reference to call record
    call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Who performed the action (admin email / "system")
    performed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<CreditTransaction type={self.transaction_type} amount=₹{self.amount:.2f}>"
