"""
backend/services/credit_service.py — Credit balance read-out.

CREDIT ENFORCEMENT IS OFF FOR THIS MVP PHASE.

No call is gated, refused, throttled, or ended because of a clinic's credit
balance — for any clinic, regardless of a zero, negative, or suspended balance.
Concretely:
  • check_call_allowed() / has_sufficient_balance() always allow. They are kept
    (rather than deleted) so that any caller — now or added later — cannot
    reintroduce a gate by accident; there is no code path that returns "denied".
  • deduct_call_credits() no longer exists. Calls do not write to the ledger, so
    nothing can drive a balance negative or auto-suspend a clinic.
  • The `clinic_credits` / `credit_transactions` tables are untouched and still
    readable (the clinic dashboard's balance card reads them), just never
    enforced or debited.

To re-enable billing later, restore the balance comparison in
check_call_allowed() and the post-call deduction in
backend/agent/processors/call_logger_processor.py — those are the only two
places that ever enforced credits.
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.clinic_credits import ClinicCredits, CreditTransaction

logger = logging.getLogger(__name__)

# Default rate: ₹1.50 per minute of voice call. Informational only while
# enforcement is off — shown in the dashboard, never charged.
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
        """Always True — credit enforcement is off (see module docstring).

        Deliberately does not read the balance: there must be no balance-derived
        way for this to return False.
        """
        return True

    @staticmethod
    async def check_call_allowed(
        db: AsyncSession,
        tenant_id: str,
        max_duration_seconds: int = 300,
    ) -> dict:
        """Always allows the call — credit enforcement is off for this MVP phase.

        Every clinic is allowed regardless of balance (zero, negative, or
        suspended). No DB read happens here at all, so there is no value in
        `clinic_credits` — including is_active=False — that can block a call, and
        a DB hiccup can't turn into a refused call either.

        Kept as an always-allow stub rather than deleted so an existing or future
        caller can't silently resurrect a gate. Shape is unchanged for callers:
          {allowed, reason, balance, required, rate_per_minute, is_active}
        """
        return {
            "allowed": True,
            "reason": "credit_enforcement_disabled",
            "balance": 0.0,
            "required": 0.0,
            "rate_per_minute": 0.0,
            "is_active": True,
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
