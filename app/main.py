import json
from pathlib import Path
from typing import Any
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import run_concierge_turn
from app.booking import create_or_get_booking
from app.db import check_db_connection, get_db
from app.models import Clinician, ConversationThread, Escalation, Patient

app = FastAPI(title="MicroHeal Care Concierge")


class ChatRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    patient_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    request_id: str | None = None
    book_slot: dict[str, str] | None = None


class BookingRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    thread_id: str = Field(default="default_thread")
    patient_id: str = Field(..., min_length=1)
    clinician_id: str = Field(..., min_length=1)
    service: str = Field(default="DOCTOR")
    subtype: str = Field(default="GENERAL_PHYSICIAN")
    slot_start: str = Field(..., min_length=1)
    notes: str = Field(default="")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    db_ok = await check_db_connection()
    if not db_ok:
        raise HTTPException(status_code=503, detail={"status": "error", "db": "disconnected"})
    return {"status": "ok", "db": "connected"}


@app.get("/v1/patients")
async def list_patients(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(select(Patient))).scalars().all()
    return [
        {
            "id": p.id,
            "society_id": p.society_id,
            "name": p.name,
            "language": p.language,
            "flat": p.flat,
        }
        for p in rows
    ]


@app.get("/v1/clinicians")
async def list_clinicians(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(select(Clinician))).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "title": c.title,
            "service": c.service,
            "subtypes": c.subtypes,
            "hospital": c.hospital,
            "slot_minutes": c.slot_minutes,
            "availability": c.availability,
        }
        for c in rows
    ]


@app.post("/v1/bookings")
async def create_booking_endpoint(payload: BookingRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await create_or_get_booking(
            db,
            request_id=payload.request_id,
            thread_id=payload.thread_id,
            patient_id=payload.patient_id,
            clinician_id=payload.clinician_id,
            service=payload.service,
            subtype=payload.subtype,
            slot_start=payload.slot_start,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/v1/chat")
async def chat_endpoint(payload: ChatRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await run_concierge_turn(
            db,
            thread_id=payload.thread_id,
            patient_id=payload.patient_id,
            message=payload.message,
            request_id=payload.request_id,
            book_slot=payload.book_slot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/chat/stream")
async def chat_stream_endpoint(payload: ChatRequest, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    try:
        result = await run_concierge_turn(
            db,
            thread_id=payload.thread_id,
            patient_id=payload.patient_id,
            message=payload.message,
            request_id=payload.request_id,
            book_slot=payload.book_slot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def event_generator():
        for tc in result.get("tool_calls", []):
            yield f"event: tool_call\ndata: {json.dumps(tc)}\n\n"
        yield f"event: message\ndata: {json.dumps({'reply': result['reply'], 'citations': result.get('citations', []), 'booking': result.get('booking'), 'escalation_id': result.get('escalation_id')})}\n\n"
        yield f"event: done\ndata: {json.dumps({'thread_id': result['thread_id'], 'request_id': result['request_id']})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/v1/threads/{thread_id}")
async def get_thread(thread_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = (
        select(ConversationThread)
        .options(selectinload(ConversationThread.messages))
        .where(ConversationThread.thread_id == thread_id)
    )
    thread = (await db.execute(stmt)).scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    escalations = (
        await db.execute(select(Escalation).where(Escalation.thread_id == thread_id))
    ).scalars().all()

    return {
        "thread_id": thread.thread_id,
        "patient_id": thread.patient_id,
        "society_id": thread.society_id,
        "state": thread.state_json or {},
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "metadata": m.metadata_json or {},
                "created_at": m.created_at.isoformat() if getattr(m, "created_at", None) else None,
            }
            for m in (thread.messages or [])
        ],
        "escalations": [
            {
                "id": e.id,
                "level": e.level,
                "severity": getattr(e, "severity", e.level),
                "sla_minutes": e.sla_minutes,
                "status": e.status,
                "trigger_message": e.trigger_message,
                "emergency_contact": e.emergency_contact,
                "created_at": e.created_at.isoformat() if getattr(e, "created_at", None) else None,
            }
            for e in escalations
        ],
    }

@app.get("/", response_class=HTMLResponse)
async def ui_home() -> HTMLResponse:
    html_path = Path(__file__).resolve().parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>MicroHeal Care Concierge</h1><p>UI template not found.</p>")
