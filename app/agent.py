import inspect
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking import BookingConflictError, create_or_get_booking
from app.models import ConversationThread, Escalation, Patient, Society, ThreadMessage
from app.rag import retrieve_corpus_chunks
from app.safety import assess_safety


async def _call_retrieve_corpus_chunks(
    session: AsyncSession, message: str, *, society: Society | None, top_k: int = 3
) -> list[dict[str, Any]]:
    res = retrieve_corpus_chunks(session, message, society=society, top_k=top_k)
    if inspect.isawaitable(res):
        return await res
    return res


async def _ensure_thread(
    session: AsyncSession,
    *,
    thread_id: str,
    patient_id: str,
    society_id: str,
    req_id: str,
) -> ConversationThread:
    thread = await session.get(ConversationThread, thread_id)
    if not thread:
        thread = ConversationThread(
            thread_id=thread_id,
            patient_id=patient_id,
            society_id=society_id,
            state_json={"turns": 0, "last_request_id": req_id},
        )
        session.add(thread)
        await session.flush()
    return thread


async def run_concierge_turn(
    session: AsyncSession,
    *,
    thread_id: str,
    patient_id: str,
    message: str,
    request_id: str | None = None,
    book_slot: dict[str, str] | None = None,
    classifier_fn=None,
) -> dict[str, Any]:
    req_id = request_id or f"req_{uuid.uuid4().hex[:12]}"

    patient = await session.get(Patient, patient_id)
    if not patient:
        raise ValueError(f"Patient not found: {patient_id}")

    society = await session.get(Society, patient.society_id)
    patient_soc_id = patient.society_id

    thread = await _ensure_thread(
        session,
        thread_id=thread_id,
        patient_id=patient_id,
        society_id=patient_soc_id,
        req_id=req_id,
    )

    session.add(
        ThreadMessage(
            thread_id=thread_id,
            role="user",
            content=message,
            metadata_json={"request_id": req_id},
        )
    )

    tool_calls: list[dict[str, Any]] = []
    safety = assess_safety(message, classifier_fn=classifier_fn)
    tool_calls.append(
        {
            "tool": "assess_safety",
            "level": safety.level,
            "call_112": safety.call_112,
            "sla_minutes": safety.sla_minutes,
        }
    )

    if safety.level is not None:
        emergency_phone = ""
        hospital_name = ""
        if society and isinstance(society.hospital, dict):
            emergency_phone = society.hospital.get("emergency_phone", "")
            hospital_name = society.hospital.get("name", "")

        esc_id = f"esc_{uuid.uuid4().hex[:12]}"
        session.add(
            Escalation(
                id=esc_id,
                thread_id=thread_id,
                patient_id=patient_id,
                society_id=patient_soc_id,
                level=safety.level,
                sla_minutes=safety.sla_minutes or 5,
                status="OPEN",
                trigger_message=message,
                emergency_contact=f"{hospital_name} {emergency_phone}".strip(),
            )
        )
        tool_calls.append({"tool": "create_escalation", "escalation_id": esc_id, "level": safety.level})

        if safety.level == 1:
            reply = (
                f"CRITICAL ESCALATION (Level 1 — {esc_id}): Please call **112** immediately and do not wait "
                f"for a callback. A nurse will call back within {safety.sla_minutes} minutes and "
                f"{hospital_name or 'your society hospital'} emergency desk ({emergency_phone or 'emergency line'}) "
                f"has been alerted."
            )
        elif safety.level == 2:
            reply = (
                f"URGENT ESCALATION (Level 2 — {esc_id}): A nurse will call you back within "
                f"{safety.sla_minutes} minutes. If symptoms worsen or become life-threatening, call **112** "
                f"or contact {hospital_name or 'the hospital'} ({emergency_phone})."
            )
        else:
            reply = (
                f"ROUTINE ESCALATION (Level 3 — {esc_id}): Logged for nurse review within "
                f"{safety.sla_minutes} minutes during wellness-room hours (or by 10:00 next morning if after hours)."
            )

        thread.state_json = {
            **dict(thread.state_json or {}),
            "turns": int((thread.state_json or {}).get("turns", 0)) + 1,
            "last_escalation_id": esc_id,
            "last_request_id": req_id,
        }
        await _ensure_thread(
            session,
            thread_id=thread_id,
            patient_id=patient_id,
            society_id=patient_soc_id,
            req_id=req_id,
        )
        session.add(
            ThreadMessage(
                thread_id=thread_id,
                role="assistant",
                content=reply,
                metadata_json={"escalation_id": esc_id, "tool_calls": tool_calls, "request_id": req_id},
            )
        )
        await session.commit()
        return {
            "thread_id": thread_id,
            "request_id": req_id,
            "reply": reply,
            "escalation_id": esc_id,
            "booking": None,
            "citations": [],
            "tool_calls": tool_calls,
        }

    booking_result: dict[str, Any] | None = None
    if book_slot:
        try:
            booking_result = await create_or_get_booking(
                session,
                request_id=req_id,
                thread_id=thread_id,
                patient_id=patient_id,
                clinician_id=book_slot["clinician_id"],
                service=book_slot.get("service", "DOCTOR"),
                subtype=book_slot.get("subtype", "GENERAL_PHYSICIAN"),
                slot_start=book_slot["slot_start"],
                notes=book_slot.get("notes", ""),
            )
            tool_calls.append({"tool": "book_slot", **booking_result})
        except BookingConflictError as exc:
            tool_calls.append({"tool": "book_slot", "error": str(exc)})
            reply = f"Unable to complete booking: {exc}"
            await _ensure_thread(
                session,
                thread_id=thread_id,
                patient_id=patient_id,
                society_id=patient_soc_id,
                req_id=req_id,
            )
            session.add(
                ThreadMessage(
                    thread_id=thread_id,
                    role="assistant",
                    content=reply,
                    metadata_json={"tool_calls": tool_calls, "request_id": req_id},
                )
            )
            await session.commit()
            return {
                "thread_id": thread_id,
                "request_id": req_id,
                "reply": reply,
                "escalation_id": None,
                "booking": None,
                "citations": [],
                "tool_calls": tool_calls,
            }

    chunks = await _call_retrieve_corpus_chunks(session, message, society=society, top_k=3)
    tool_calls.append(
        {
            "tool": "retrieve_corpus",
            "society_id": patient_soc_id,
            "doc_ids": [c["doc_id"] for c in chunks],
        }
    )

    citations = [
        {"doc_id": c["doc_id"], "title": c["title"], "section": c["section"], "chunk_id": c["chunk_id"]}
        for c in chunks
    ]

    lower_q = message.lower()
    if booking_result:
        reply = (
            f"Booking confirmed — Booking ID: **{booking_result['booking_id']}** "
            f"(Clinician: {booking_result['clinician_id']}, Slot: {booking_result['slot_start']}, "
            f"Request ID: {booking_result['request_id']})."
        )
    elif chunks:
        top = chunks[0]
        if "sunday" in lower_q and "wellness" in lower_q:
            sunday_lines = [
                line.strip()
                for line in top["content"].splitlines()
                if "sunday" in line.lower()
            ]
            snippet = " | ".join(sunday_lines) if sunday_lines else top["content"][:300]
            reply = (
                f"For **{society.name if society else patient_soc_id}**: {snippet} "
                f"[{top['title']} > {top['section']}]"
            )
        else:
            excerpts = "\n".join(
                f"- **{c['title']}** ({c['section']}): {c['content'][:220]}" for c in chunks
            )
            reply = f"Here is what I found for **{society.name if society else patient_soc_id}**:\n{excerpts}"
    else:
        reply = "I could not find a matching policy or annex entry for your question. Please contact the clinic desk."

    thread = await _ensure_thread(
        session,
        thread_id=thread_id,
        patient_id=patient_id,
        society_id=patient_soc_id,
        req_id=req_id,
    )
    thread.state_json = {
        **dict(thread.state_json or {}),
        "turns": int((thread.state_json or {}).get("turns", 0)) + 1,
        "last_request_id": req_id,
        "last_booking_id": booking_result["booking_id"] if booking_result else (thread.state_json or {}).get("last_booking_id"),
    }

    session.add(
        ThreadMessage(
            thread_id=thread_id,
            role="assistant",
            content=reply,
            metadata_json={"citations": citations, "booking": booking_result, "tool_calls": tool_calls, "request_id": req_id},
        )
    )
    await session.commit()

    return {
        "thread_id": thread_id,
        "request_id": req_id,
        "reply": reply,
        "escalation_id": None,
        "booking": booking_result,
        "citations": citations,
        "tool_calls": tool_calls,
    }