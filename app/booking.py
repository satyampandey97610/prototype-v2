import hashlib
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinician, Patient, ServiceRequest, Society


class BookingConflictError(Exception):
    pass


class SlotValidationError(ValueError):
    pass


_WEEKDAY_ALIASES = {
    "mon": "monday",
    "monday": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "tuesday": "tuesday",
    "wed": "wednesday",
    "wednesday": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "thursday": "thursday",
    "fri": "friday",
    "friday": "friday",
    "sat": "saturday",
    "saturday": "saturday",
    "sun": "sunday",
    "sunday": "sunday",
}


def _normalize_day(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    return _WEEKDAY_ALIASES.get(val, val)


def _parse_iso_dt(value: str, default_tz: str = "Asia/Kolkata") -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(default_tz))
    return dt


def validate_clinician_slot(
    clinician: Clinician,
    scheduled_for: str,
    *,
    society: Society | None = None,
) -> str:
    """Validate that scheduled_for aligns with an availability window and slot_minutes step."""
    tz_name = society.timezone if (society and society.timezone) else "Asia/Kolkata"
    dt = _parse_iso_dt(scheduled_for, default_tz=tz_name)
    dt_local = dt.astimezone(ZoneInfo(tz_name))
    weekday_name = dt_local.strftime("%A").lower()
    minutes_from_midnight = dt_local.hour * 60 + dt_local.minute

    slot_minutes = int(getattr(clinician, "slot_minutes", 0) or 0)
    if slot_minutes <= 0:
        raise SlotValidationError(f"Invalid slot_minutes for clinician {clinician.id}.")

    windows = clinician.availability if isinstance(clinician.availability, list) else []
    if not windows:
        return dt.isoformat()

    for win in windows:
        if not isinstance(win, dict):
            continue
        win_day = _normalize_day(win.get("day") or win.get("weekday"))
        if win_day and win_day != weekday_name:
            continue
        win_soc = win.get("society_id")
        if win_soc and society and win_soc != society.id:
            continue

        start_str = str(win.get("start", "00:00"))
        end_str = str(win.get("end", "23:59"))
        sh, sm = [int(x) for x in start_str.split(":")[:2]]
        eh, em = [int(x) for x in end_str.split(":")[:2]]
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em

        if minutes_from_midnight < start_mins or (minutes_from_midnight + slot_minutes) > end_mins:
            continue
        if (minutes_from_midnight - start_mins) % slot_minutes == 0:
            return dt.isoformat()

    raise SlotValidationError(
        f"Requested time {scheduled_for} is outside clinician {clinician.name} ({clinician.id}) "
        f"available slots ({slot_minutes}-minute grid)."
    )


def _format_booking(sr: ServiceRequest, *, idempotent_replay: bool) -> dict[str, Any]:
    return {
        "booking_id": sr.id,
        "id": sr.id,
        "request_id": sr.request_id,
        "thread_id": sr.thread_id,
        "patient_id": sr.patient_id,
        "society_id": sr.society_id,
        "clinician_id": sr.clinician_id,
        "service": sr.service,
        "subtype": sr.subtype,
        "scheduled_for": sr.scheduled_for,
        "slot_start": sr.scheduled_for,
        "status": sr.status,
        "idempotent_replay": idempotent_replay,
    }


async def create_or_get_booking(
    session: AsyncSession,
    *,
    request_id: str,
    thread_id: str = "default_thread",
    patient_id: str,
    clinician_id: str,
    service: str = "DOCTOR",
    subtype: str = "GENERAL_PHYSICIAN",
    slot_start: str | None = None,
    scheduled_for: str | None = None,
    notes: str = "",
    validate_slot: bool = True,
) -> dict[str, Any]:
    """Idempotent booking on service_requests with advisory/row locks and clear error handling."""
    target_slot = scheduled_for or slot_start
    if not target_slot:
        raise SlotValidationError("Please provide a valid appointment date and time slot.")

    existing = (
        await session.execute(select(ServiceRequest).where(ServiceRequest.request_id == request_id))
    ).scalar_one_or_none()
    if existing:
        return _format_booking(existing, idempotent_replay=True)

    patient = await session.get(Patient, patient_id)
    if not patient:
        raise ValueError(f"We could not find patient record '{patient_id}'.")

    clinician = await session.get(Clinician, clinician_id)
    if not clinician:
        raise ValueError(f"We could not find clinician '{clinician_id}'.")

    society = await session.get(Society, patient.society_id)

    if validate_slot:
        try:
            target_slot = validate_clinician_slot(clinician, target_slot, society=society)
        except SlotValidationError as exc:
            raise ValueError(str(exc)) from exc

    lock_key_hex = hashlib.sha256(f"{clinician_id}:{target_slot}".encode("utf-8")).hexdigest()[:15]
    lock_key_int = int(lock_key_hex, 16)
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key_int})
    except Exception:
        pass

    conflict = (
        (
            await session.execute(
                select(ServiceRequest)
                .where(
                    ServiceRequest.clinician_id == clinician_id,
                    ServiceRequest.scheduled_for == target_slot,
                    ServiceRequest.status.in_(("CONFIRMED", "PENDING")),
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if conflict:
        raise BookingConflictError(
            f"That time slot ({target_slot}) is already booked for {clinician.name}. Please choose another slot."
        )

    sr = ServiceRequest(
        id=f"sr_{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        thread_id=thread_id,
        patient_id=patient.id,
        society_id=patient.society_id,
        clinician_id=clinician.id,
        service=service.upper(),
        subtype=subtype.upper(),
        scheduled_for=target_slot,
        status="CONFIRMED",
        notes=notes,
    )
    session.add(sr)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        replay = (
            await session.execute(select(ServiceRequest).where(ServiceRequest.request_id == request_id))
        ).scalar_one_or_none()
        if replay:
            return _format_booking(replay, idempotent_replay=True)
        raise BookingConflictError(
            f"That time slot ({target_slot}) is already taken for {clinician.name}. Please choose another slot."
        )

    await session.refresh(sr)
    return _format_booking(sr, idempotent_replay=False)