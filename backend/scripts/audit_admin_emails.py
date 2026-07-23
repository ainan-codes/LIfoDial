# -*- coding: utf-8 -*-
"""
Audit clinic (tenant) login emails — supports audit P2.

Reports, against whatever DATABASE_URL the app is configured with:
  1. Tenants with NO usable login (admin_email or admin_password NULL/blank).
     These are the clinics broken by the "password never persisted" bug — they
     need a manual credential reset.
  2. Tenants whose admin_email matches the generic admin@<slug>.lifodial.com
     pattern (likely never a real deliverable inbox).
  3. Case-insensitive duplicate admin_emails — these BLOCK the new unique index
     and make clinic_login ambiguous; each must be given a distinct email.

Read-only. Prints a summary and exits non-zero if any blocking duplicates exist.

Run:
    python -m backend.scripts.audit_admin_emails
"""
import asyncio
import sys

from sqlalchemy import text

from backend.db import AsyncSessionLocal, db_label


async def main() -> int:
    print(f"Auditing clinic login emails on: {db_label}\n")
    async with AsyncSessionLocal() as s:
        total = (await s.execute(text("SELECT count(*) FROM tenants"))).scalar_one()

        no_login = (await s.execute(text(
            "SELECT id, clinic_name, admin_email FROM tenants "
            "WHERE admin_email IS NULL OR btrim(admin_email) = '' "
            "   OR admin_password IS NULL OR btrim(admin_password) = '' "
            "ORDER BY clinic_name"
        ))).fetchall()

        generic = (await s.execute(text(
            "SELECT id, clinic_name, admin_email FROM tenants "
            "WHERE lower(admin_email) LIKE 'admin@%.lifodial.com' "
            "ORDER BY clinic_name"
        ))).fetchall()

        dupes = (await s.execute(text(
            "SELECT lower(admin_email) AS email, count(*) AS n FROM tenants "
            "WHERE admin_email IS NOT NULL AND btrim(admin_email) <> '' "
            "GROUP BY lower(admin_email) HAVING count(*) > 1 "
            "ORDER BY n DESC"
        ))).fetchall()

    print(f"Total clinics: {total}\n")

    print(f"[1] Clinics with NO usable login (need credential reset): {len(no_login)}")
    for r in no_login:
        print(f"      - {r.clinic_name!r}  id={r.id}  email={r.admin_email!r}")

    print(f"\n[2] Clinics with a generic admin@<slug>.lifodial.com email: {len(generic)}")
    for r in generic:
        print(f"      - {r.clinic_name!r}  id={r.id}  email={r.admin_email!r}")

    print(f"\n[3] Colliding (case-insensitive duplicate) emails: {len(dupes)}")
    for r in dupes:
        print(f"      - {r.email!r} used by {r.n} clinics")

    if dupes:
        print("\nRESULT: duplicates exist — resolve these before applying migration "
              "a1b2c3d4e5f6 (they would block the unique index).")
        return 1
    print("\nRESULT: no colliding emails — migration a1b2c3d4e5f6 can be applied safely.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
