import asyncio
import uuid
import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import delete, func, select

from app.classifier import SafetyAndIntentClassifier
from app.graph import build_care_concierge_graph
from app.models import ServiceRequest, SymptomLog


@pytest.mark.asyncio
async def test_concurrent_double_booking_prevented_in_db(db_session_factory, toolbox):
    """Two simultaneous bookings for one clinician & slot -> 1 row in DB, other gets clear error."""
    slot = "2027-02-16T10:00:00+05:30"  # Tuesday 10:00 at Shivalik Greens (Dr. Arvind Khanna)
    async with db_session_factory() as s:
        await s.execute(delete(ServiceRequest).where(ServiceRequest.scheduled_for == slot))
        await s.commit()

    req1 = f"req-{uuid.uuid4()}"
    req2 = f"req-{uuid.uuid4()}"

    r1, r2 = await asyncio.gather(
        toolbox.book_service("pat_001", "DOCTOR", "CARDIOLOGY", slot, req1),
        toolbox.book_service("pat_003", "DOCTOR", "CARDIOLOGY", slot, req2),
    )

    results = [r1, r2]
    confirmed = [r for r in results if r.get("status") == "CONFIRMED"]
    conflicts = [r for r in results if r.get("status") == "SLOT_TAKEN"]

    assert len(confirmed) == 1
    assert len(conflicts) == 1
    assert "already booked" in conflicts[0]["error"].lower()

    async with db_session_factory() as s:
        cnt = await s.scalar(
            select(func.count()).select_from(ServiceRequest).where(ServiceRequest.scheduled_for == slot)
        )
        assert cnt == 1


@pytest.mark.asyncio
async def test_duplicate_request_id_returns_same_booking(db_session_factory, pg_checkpointer, toolbox):
    """Sending the exact same request_id twice creates 1 row and both replies report that booking."""
    slot = "2027-02-23T10:30:00+05:30"
    async with db_session_factory() as s:
        await s.execute(delete(ServiceRequest).where(ServiceRequest.scheduled_for == slot))
        await s.commit()

    shared_req_id = f"req-{uuid.uuid4()}"
    b1 = await toolbox.book_service("pat_001", "DOCTOR", "CARDIOLOGY", slot, shared_req_id)
    b2 = await toolbox.book_service("pat_001", "DOCTOR", "CARDIOLOGY", slot, shared_req_id)

    assert b1["status"] == "CONFIRMED"
    assert b2["status"] == "CONFIRMED"
    assert b1["id"] == b2["id"]
    assert b2.get("idempotent_replay") is True

    async with db_session_factory() as s:
        cnt = await s.scalar(
            select(func.count()).select_from(ServiceRequest).where(ServiceRequest.request_id == shared_req_id)
        )
        assert cnt == 1


@pytest.mark.asyncio
async def test_db_check_constraint_on_symptom_severity(db_session_factory):
    """DB CHECK constraint rejects severity outside 1-5."""
    from sqlalchemy.exc import IntegrityError

    async with db_session_factory() as s:
        bad = SymptomLog(
            id=f"sym_{uuid.uuid4().hex[:8]}",
            patient_id="pat_001",
            society_id="soc_shivalik",
            symptom="headache",
            severity=9,
            request_id=f"req-{uuid.uuid4()}",
        )
        s.add(bad)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()