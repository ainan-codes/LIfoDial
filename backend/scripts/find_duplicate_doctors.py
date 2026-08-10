# -*- coding: utf-8 -*-
"""
Find duplicate doctor rows — supports the doctor-dedup migration.

Reports, against whatever DATABASE_URL the app is configured with:
  1. Doctors sharing the same (tenant_id, his_doctor_id) where his_doctor_id
     is set — these BLOCK the new uq_doctors_tenant_his_id unique index.
  2. Doctors sharing the same (tenant_id, lower(trim(name))) — not blocked by
     any DB constraint (two real doctors can share a common name), but
     surfaced so a human can tell a genuine duplicate from a coincidence.

For every hit, prints the full row (id, name, specialization, his_doctor_id,
is_available, created_at) plus how many appointments reference that doctor
id, so a reviewer can tell which duplicate is the "keeper".

Read-only — no writes, no merges, no deletes. Exits non-zero if any
his_doctor_id collision exists (those block the migration).

Run:
    python -m backend.scripts.find_duplicate_doctors
"""
import asyncio
import sys

from sqlalchemy import text

from backend.db import AsyncSessionLocal, db_label


async def _print_matching_rows(s, tenant_id: str, where_clause: str, params: dict) -> None:
    rows = (await s.execute(text(
        "SELECT d.id, d.name, d.specialization, d.his_doctor_id, d.is_available, "
        "d.created_at, "
        "(SELECT count(*) FROM appointments a WHERE a.doctor_id = d.id) AS appt_count "
        f"FROM doctors d WHERE d.tenant_id = :tenant_id AND {where_clause} "
        "ORDER BY d.created_at"
    ), {"tenant_id": tenant_id, **params})).fetchall()
    for r in rows:
        print(
            f"        id={r.id}  name={r.name!r}  spec={r.specialization!r}  "
            f"his_doctor_id={r.his_doctor_id!r}  is_available={r.is_available}  "
            f"appointments={r.appt_count}  created_at={r.created_at}"
        )


async def main() -> int:
    print(f"Auditing doctors for duplicates on: {db_label}\n")
    async with AsyncSessionLocal() as s:
        total = (await s.execute(text("SELECT count(*) FROM doctors"))).scalar_one()

        his_id_dupes = (await s.execute(text(
            "SELECT tenant_id, his_doctor_id, count(*) AS n "
            "FROM doctors WHERE his_doctor_id IS NOT NULL "
            "GROUP BY tenant_id, his_doctor_id HAVING count(*) > 1 "
            "ORDER BY n DESC"
        ))).fetchall()

        name_dupes = (await s.execute(text(
            "SELECT tenant_id, lower(btrim(name)) AS norm_name, count(*) AS n "
            "FROM doctors GROUP BY tenant_id, lower(btrim(name)) HAVING count(*) > 1 "
            "ORDER BY n DESC"
        ))).fetchall()

        print(f"Total doctors: {total}\n")

        print(f"[1] Duplicate (tenant_id, his_doctor_id) — BLOCKS uq_doctors_tenant_his_id: {len(his_id_dupes)}")
        for r in his_id_dupes:
            print(f"    tenant={r.tenant_id}  his_doctor_id={r.his_doctor_id!r}  count={r.n}")
            await _print_matching_rows(s, r.tenant_id, "d.his_doctor_id = :his_id", {"his_id": r.his_doctor_id})

        print(f"\n[2] Duplicate (tenant_id, name) — not DB-blocked, human judgment call: {len(name_dupes)}")
        for r in name_dupes:
            print(f"    tenant={r.tenant_id}  name={r.norm_name!r}  count={r.n}")
            await _print_matching_rows(s, r.tenant_id, "lower(btrim(d.name)) = :norm_name", {"norm_name": r.norm_name})

    if his_id_dupes:
        print(
            "\nRESULT: his_doctor_id collisions exist — resolve these (merge/delete the "
            "non-keeper row) before applying the uq_doctors_tenant_his_id migration."
        )
        return 1
    print("\nRESULT: no his_doctor_id collisions — the unique index can be applied safely.")
    if name_dupes:
        print(f"({len(name_dupes)} name-only duplicate group(s) found — review above; not a migration blocker.)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
