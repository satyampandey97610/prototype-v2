import uuid
from typing import Any
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, Clinician, Patient


class BookingConflictError(Exception):
    """Raised when a clinician slot is already booked or conflicts with an active booking."""


def _format_booking(b: Booking, idempotent_replay: bool = False) -> dict[str, Any]:
    return {
        "id": b.id,
        "booking_id": b.id,
        "request_id": b.request_id,
        "thread_id": b.thread_id,
        "status": b.status,
        "clinician_id": b.clinician_id,
        "patient_id": b.patient_id,
        "society_id": b.society_id,
        "slot_start": b.slot_start,
        "scheduled_for": b.slot_start,
        "service": b.service,
        "subtype": b.subtype,
        "idempotent_replay": idempotent_replay,
    }


async def create_or_get_booking(
    session: AsyncSession,
    *,
    request_id: str,
    thread_id: str = "default_thread",
    patient_id: str,
    clinician_id: str = "cln_01",
    service: str = "DOCTOR",
    subtype: str = "GENERAL_PHYSICIAN",
    slot_start: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if not slot_start:
        raise ValueError("slot_start is required to create a booking.")

    # Idempotency check by request_id first
    existing = (
        await session.execute(select(Booking).where(Booking.request_id == request_id))
    ).scalar_one_or_none()
    if existing:
        return _format_booking(existing, idempotent_replay=True)

    patient = await session.get(Patient, patient_id)
    if not patient:
        raise ValueError(f"Unknown patient_id: {patient_id}")

    clinician = await session.get(Clinician, clinician_id)
    if not clinician:
        raise ValueError(f"Unknown clinician_id: {clinician_id}")

    # Transaction-scoped advisory lock serializes concurrent inserts for non-existent slots
    lock_key = f"{clinician_id}:{slot_start}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": lock_key},
    )

    # Row-level lock on any existing active rows for this clinician & slot
    lock_stmt = (
        select(Booking)
        .where(
            Booking.clinician_id == clinician_id,
            Booking.slot_start == slot_start,
            Booking.status != "CANCELLED",
        )
        .with_for_update()
    )
    conflict = (await session.execute(lock_stmt)).scalars().first()
    if conflict:
        raise BookingConflictError(
            f"Slot {slot_start} for clinician {clinician_id} is already booked ({conflict.id})."
        )

    booking = Booking(
        id=f"bkg_{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        thread_id=thread_id,
        patient_id=patient.id,
        society_id=patient.society_id,
        clinician_id=clinician.id,
        service=service.upper(),
        subtype=subtype.upper(),
        slot_start=slot_start,
        status="CONFIRMED",
        notes=notes or "",
    )
    session.add(booking)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        replay = (
            await session.execute(select(Booking).where(Booking.request_id == request_id))
        ).scalar_one_or_none()
        if replay:
            return _format_booking(replay, idempotent_replay=True)
        raise BookingConflictError(
            f"Slot {slot_start} for clinician {clinician_id} is already booked."
        ) from exc

    await session.refresh(booking)
    return _format_booking(booking, idempotent_replay=False)