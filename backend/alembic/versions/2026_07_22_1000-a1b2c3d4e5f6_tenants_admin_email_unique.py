"""tenants: case-insensitive unique index on admin_email (login identity)

Revision ID: a1b2c3d4e5f6
Revises: ec71d78f791a
Create Date: 2026-07-22 10:00:00.000000+00:00

The clinic login is identified solely by tenants.admin_email (see
agents.py::clinic_login, which does scalar_one_or_none on it). Two clinics
sharing an email therefore make login ambiguous — scalar_one_or_none raises and
the login 500s. This adds the case-insensitive uniqueness guarantee so that can
never happen "whatever the source of the address" (audit P2).

If pre-existing case-insensitive duplicates are present the upgrade FAILS LOUDLY
and lists them, rather than silently mangling data — resolve those clinics'
emails (see backend/scripts/audit_admin_emails.py) and re-run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'ec71d78f791a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        # Refuse to create the index over dirty data — report the collisions.
        dupes = bind.execute(sa.text("""
            SELECT lower(admin_email) AS email, count(*) AS n
            FROM tenants
            WHERE admin_email IS NOT NULL AND btrim(admin_email) <> ''
            GROUP BY lower(admin_email)
            HAVING count(*) > 1
        """)).fetchall()
        if dupes:
            listing = ", ".join(f"{r.email} (x{r.n})" for r in dupes)
            raise RuntimeError(
                "Cannot add unique index ux_tenants_admin_email_lower: "
                f"existing clinics share an admin email: {listing}. "
                "Reset the colliding clinics' emails (each clinic login needs a "
                "distinct email), then re-run this migration."
            )
        # NULL/empty admin_email is allowed to repeat (lower(NULL) is NULL, and
        # SQL unique indexes permit multiple NULLs) — clinics without a login
        # email do not collide.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_admin_email_lower "
            "ON tenants (lower(admin_email))"
        )
    else:
        # SQLite (local dev only) — best-effort, non-fatal.
        import contextlib
        with contextlib.suppress(Exception):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_admin_email_lower "
                "ON tenants (admin_email COLLATE NOCASE)"
            )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_tenants_admin_email_lower")
