"""
backend/services/credit_service.py — Credit balance management.

Handles:
  • Balance checks before call start
  • Per-minute deduction after call ends
  • Top-up / adjustment by super admin
  • Transaction logging (immutable ledger)
"""
import logging
import math
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.clinic_credits import ClinicCredits, CreditTransaction

logger = logging.getLogger(__name__)

# Default rate: ₹1.50 per minute of voice call
DEFAULT_RATE_PER_MINUTE = 1.50


class CreditService:
    """Stateless service — all methods take an AsyncSession."""

    @staticmethod
    async def get_or_create_balance(
        db: AsyncSession,
        tenant_id: str,
    ) -> ClinicCredits:
        """Get credit record for tenant, creating one if missing."""
        result = await db.execute(
            select(ClinicCredits).where(ClinicCredits.tenant_id == tenant_id)
        )
        credits = result.scalar_one_or_none()

        if not credits:
            credits = ClinicCredits(
                tenant_id=tenant_id,
                balance=0.0,
                rate_per_minute=DEFAULT_RATE_PER_MINUTE,
            )
            db.add(credits)
            await db.flush()
            logger.info("Created credit record for tenant %s", tenant_id)

        return credits

    @staticmethod
    async def has_sufficient_balance(
        db: AsyncSession,
        tenant_id: str,
        min_minutes: float = 1.0,
    ) -> bool:
        """Check if clinic has enough credits for at least `min_minutes`."""
        credits = await CreditService.get_or_create_balance(db, tenant_id)
        required = credits.rate_per_minute * min_minutes
        return credits.balance >= required

    @staticmethod
    async def deduct_call_credits(
        db: AsyncSession,
        tenant_id: str,
        duration_seconds: int,
        call_id: str | None = None,
    ) -> dict:
        """
        Deduct credits after a call ends.
        Rounds UP to nearest second, then bills per minute.
        Returns dict with deduction details.
        """
        credits = await CreditService.get_or_create_balance(db, tenant_id)
        rate = credits.rate_per_minute

        # Calculate cost: ceil to nearest minute for billing
        minutes = math.ceil(duration_seconds / 60) if duration_seconds > 0 else 0
        cost = round(rate * minutes, 2)

        if cost <= 0:
            return {
                "deducted": 0,
                "balance_after": credits.balance,
                "duration_seconds": duration_seconds,
                "minutes_billed": 0,
            }

        # Atomic decrement — avoids the read-modify-write lost-update race under
        # concurrent calls. The single UPDATE is evaluated by the DB, not Python.
        await db.execute(
            update(ClinicCredits)
            .where(ClinicCredits.tenant_id == tenant_id)
            .values(
                balance=ClinicCredits.balance - cost,
                total_deducted=ClinicCredits.total_deducted + cost,
            )
        )
        # Re-read the authoritative post-update balance for the ledger entry.
        refreshed = await db.execute(
            select(ClinicCredits.balance).where(ClinicCredits.tenant_id == tenant_id)
        )
        balance_after = round(refreshed.scalar_one(), 2)

        txn = CreditTransaction(
            tenant_id=tenant_id,
            transaction_type="call_deduction",
            amount=-cost,
            balance_after=balance_after,
            description=(
                f"Call {minutes}m ({duration_seconds}s) @ ₹{rate:.2f}/min"
            ),
            call_id=call_id,
            performed_by="system",
        )
        db.add(txn)
        await db.commit()

        logger.info(
            "Credit deduction: tenant=%s cost=₹%.2f balance=₹%.2f call=%s",
            tenant_id, cost, balance_after, call_id,
        )

        return {
            "deducted": cost,
            "balance_after": balance_after,
            "duration_seconds": duration_seconds,
            "minutes_billed": minutes,
            "rate_per_minute": rate,
        }

    @staticmethod
    async def add_credits(
        db: AsyncSession,
        tenant_id: str,
        amount: float,
        description: str = "Admin top-up",
        performed_by: str = "super_admin",
    ) -> dict:
        """Add credits to a clinic's balance."""
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await CreditService.get_or_create_balance(db, tenant_id)
        credits.balance = round(credits.balance + amount, 2)
        credits.total_added = round(credits.total_added + amount, 2)

        txn = CreditTransaction(
            tenant_id=tenant_id,
            transaction_type="topup",
            amount=amount,
            balance_after=credits.balance,
            description=description,
            performed_by=performed_by,
        )
        db.add(txn)

        logger.info(
            "Credit top-up: tenant=%s amount=₹%.2f new_balance=₹%.2f by=%s",
            tenant_id, amount, credits.balance, performed_by,
        )

        return {
            "added": amount,
            "balance_after": credits.balance,
        }

    @staticmethod
    async def set_rate(
        db: AsyncSession,
        tenant_id: str,
        rate_per_minute: float,
    ) -> dict:
        """Update per-minute billing rate for a clinic."""
        if rate_per_minute < 0:
            raise ValueError("Rate must be non-negative")

        credits = await CreditService.get_or_create_balance(db, tenant_id)
        old_rate = credits.rate_per_minute
        credits.rate_per_minute = rate_per_minute

        logger.info(
            "Rate updated: tenant=%s old=₹%.2f new=₹%.2f",
            tenant_id, old_rate, rate_per_minute,
        )

        return {
            "old_rate": old_rate,
            "new_rate": rate_per_minute,
        }

    @staticmethod
    async def get_transactions(
        db: AsyncSession,
        tenant_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent transactions for a tenant."""
        result = await db.execute(
            select(CreditTransaction)
            .where(CreditTransaction.tenant_id == tenant_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
        txns = result.scalars().all()

        return [
            {
                "id": t.id,
                "type": t.transaction_type,
                "amount": t.amount,
                "balance_after": t.balance_after,
                "description": t.description,
                "call_id": t.call_id,
                "performed_by": t.performed_by,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ]

    @staticmethod
    async def get_all_balances(db: AsyncSession) -> list[dict]:
        """Get all clinic credit balances (for super admin)."""
        from backend.models.tenant import Tenant

        result = await db.execute(
            select(ClinicCredits, Tenant.clinic_name)
            .join(Tenant, ClinicCredits.tenant_id == Tenant.id)
            .order_by(Tenant.clinic_name)
        )
        rows = result.all()

        return [
            {
                "tenant_id": c.tenant_id,
                "clinic_name": name,
                "balance": c.balance,
                "rate_per_minute": c.rate_per_minute,
                "total_added": c.total_added,
                "total_deducted": c.total_deducted,
                "low_balance_threshold": c.low_balance_threshold,
                "is_active": c.is_active,
                "is_low": c.balance < c.low_balance_threshold,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c, name in rows
        ]
