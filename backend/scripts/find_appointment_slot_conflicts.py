# -*- coding: utf-8 -*-
"""
Find appointment slot conflicts — supports the conflict-safety migration.

Reports, against whatever DATABASE_URL the app is configured with, any
non-cancelled appointments sharing the same (doctor_id, slot_time) — these
BLOCK the new uq_appointments_doctor_slot_active unique index and represent
real, already-existing double-bookings that predate any conflict check.

For every hit, prints the full row (id, tenant_id, patient_name,
patient_phone, status, call_id, created_at) so a human can decide which
booking is genuine and which should be cancelled/contacted.

Read-only — no writes, no cancellations. Exits non-zero if any conflict
exists (it blocks the migration).

Run:
    python -m backend.scripts.find_appointment_slot_conflicts
"""
import asyncio
import sys

from sqlalchemy import text

from backend.db import AsyncSessionLocal, db_label


async def main() -> int:
    print(f"Auditing appointments for slot conflicts on: {db_label}\n")
    async with AsyncSessionLocal() as s:
        total = (await s.execute(text(
            "SELECT count(*) FROM appointments WHERE status <> 'cancelled'"
        ))).scalar_one()

        conflicts = (await s.execute(text(
            "SELECT doctor_id, slot_time, count(*) AS n "
            "FROM appointments WHERE status <> 'cancelled' "
            "GROUP BY doctor_id, slot_time HAVING count(*) > 1 "
            "ORDER BY n DESC"
        ))).fetchall()

        print(f"Total active (non-cancelled) appointments: {total}\n")
        print(f"Double-booked (doctor_id, slot_time) groups — BLOCKS uq_appointments_doctor_slot_active: {len(conflicts)}")

        for r in conflicts:
            print(f"    doctor_id={r.doctor_id}  slot_time={r.slot_time}  count={r.n}")
            rows = (await s.execute(text(
                "SELECT id, tenant_id, patient_name, patient_phone, status, call_id, created_at "
                "FROM appointments WHERE doctor_id = :doctor_id AND slot_time = :slot_time "
                "AND status <> 'cancelled' ORDER BY created_at"
            ), {"doctor_id": r.doctor_id, "slot_time": r.slot_time})).fetchall()
            for row in rows:
                print(
                    f"        id={row.id}  tenant={row.tenant_id}  patient={row.patient_name!r}  "
                    f"phone={row.patient_phone!r}  status={row.status!r}  call_id={row.call_id!r}  "
                    f"created_at={row.created_at}"
                )

    if conflicts:
        print(
            "\nRESULT: real double-bookings exist — resolve these (cancel the "
            "non-genuine booking and contact that patient) before applying the "
            "uq_appointments_doctor_slot_active migration."
        )
        return 1
    print("\nRESULT: no slot conflicts — the unique index can be applied safely.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
