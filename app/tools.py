import inspect
import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import run_concierge_turn
from app.booking import BookingConflictError, create_or_get_booking
from app.models import Booking, Society
from app.rag import retrieve_corpus_chunks
from app.safety import SafetyAssessment, assess_safety


class CareConciergeToolbox:
    """Toolbox interface wrapping safety, corpus retrieval, booking, and concierge execution."""

    def __init__(self, session_or_factory: Any = None, session: AsyncSession | None = None, session_factory=None):
        if session is not None or session_factory is not None:
            self.session = session
            self.session_factory = session_factory
        elif session_or_factory is not None:
            if isinstance(session_or_factory, AsyncSession) or hasattr(session_or_factory, "execute"):
                self.session = session_or_factory
                self.session_factory = None
            else:
                self.session = None
                self.session_factory = session_or_factory
        else:
            self.session = None
            self.session_factory = None

    async def _resolve_session(self, session: Any = None):
        cand = session if session is not None else (self.session if self.session is not None else self.session_factory)
        if cand is None:
            raise ValueError("Database session or session_factory is required.")

        if callable(cand):
            cand = cand()

        if hasattr(cand, "__aenter__"):
            entered = await cand.__aenter__()
            return entered, ("aexit", cand)

        if hasattr(cand, "__aiter__"):
            agen = cand
            try:
                item = await agen.__anext__()
            except StopAsyncIteration as exc:
                raise ValueError("Session async_generator yielded no session.") from exc
            if hasattr(item, "__aenter__"):
                entered = await item.__aenter__()
                return entered, ("gen_aexit", agen, item)
            return item, ("gen_close", agen, item)

        return cand, None

    async def _cleanup_session(self, cleanup: Any) -> None:
        if not cleanup:
            return
        kind = cleanup[0]
        try:
            if kind == "aexit":
                await cleanup[1].__aexit__(None, None, None)
            elif kind == "gen_aexit":
                await cleanup[2].__aexit__(None, None, None)
                try:
                    await cleanup[1].aclose()
                except Exception:
                    pass
            elif kind == "gen_close":
                item = cleanup[2]
                if hasattr(item, "close"):
                    res = item.close()
                    if inspect.isawaitable(res):
                        await res
                try:
                    await cleanup[1].aclose()
                except Exception:
                    pass
        except Exception:
            pass

    def check_safety(self, message: str, classifier_fn=None) -> SafetyAssessment:
        return assess_safety(message, classifier_fn=classifier_fn)

    async def list_service_requests(
        self,
        patient_id: str | None = None,
        society_id: str | None = None,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        db, cleanup = await self._resolve_session(session)
        try:
            stmt = select(Booking)
            if patient_id is not None:
                stmt = stmt.where(Booking.patient_id == patient_id)
            if society_id is not None:
                stmt = stmt.where(Booking.society_id == society_id)
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "request_id": r.request_id,
                    "thread_id": r.thread_id,
                    "patient_id": r.patient_id,
                    "society_id": r.society_id,
                    "clinician_id": r.clinician_id,
                    "service": r.service,
                    "subtype": r.subtype,
                    "slot_start": r.slot_start,
                    "status": r.status,
                }
                for r in rows
            ]
        finally:
            await self._cleanup_session(cleanup)

    async def search_clinic_docs(
        self,
        query: str,
        society_id: str | None = None,
        k: int = 3,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        db, cleanup = await self._resolve_session(session)
        try:
            society = await db.get(Society, society_id) if society_id else None
            chunks = await retrieve_corpus_chunks(db, query, society=society, top_k=max(k * 2, 6))
            return chunks[:k]
        finally:
            await self._cleanup_session(cleanup)

    async def search_corpus(
        self,
        query: str,
        society: Society | None = None,
        top_k: int = 3,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        db, cleanup = await self._resolve_session(session)
        try:
            return await retrieve_corpus_chunks(db, query, society=society, top_k=top_k)
        finally:
            await self._cleanup_session(cleanup)

    async def book_slot(
        self,
        request_id: str | None = None,
        thread_id: str = "default_thread",
        patient_id: str = "pat_001",
        clinician_id: str = "cln_01",
        service: str = "DOCTOR",
        subtype: str = "GENERAL_PHYSICIAN",
        slot_start: str | None = None,
        scheduled_for: str | None = None,
        notes: str = "",
        session: Any = None,
    ) -> dict[str, Any]:
        resolved_slot = slot_start or scheduled_for
        if not resolved_slot:
            raise ValueError("slot_start is required to book a slot.")
        db, cleanup = await self._resolve_session(session)
        try:
            return await create_or_get_booking(
                db,
                request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
                thread_id=thread_id,
                patient_id=patient_id,
                clinician_id=clinician_id,
                service=service,
                subtype=subtype,
                slot_start=resolved_slot,
                notes=notes,
            )
        except BookingConflictError as exc:
            return {"status": "SLOT_TAKEN", "error": str(exc)}
        finally:
            await self._cleanup_session(cleanup)

    async def book_service(
        self,
        patient_id: str,
        service: str,
        subtype: str,
        slot_start: str | None = None,
        request_id: str | None = None,
        *,
        scheduled_for: str | None = None,
        clinician_id: str | None = None,
        thread_id: str = "default_thread",
        notes: str = "",
        session: Any = None,
    ) -> dict[str, Any]:
        resolved_slot = slot_start or scheduled_for
        if not resolved_slot:
            raise ValueError("slot_start is required to book a service.")
        resolved_clinician = clinician_id
        if not resolved_clinician:
            clinician_map = {
                "pat_009": "cln_03",
                "pat_003": "cln_01",
                "pat_010": "cln_02",
            }
            resolved_clinician = clinician_map.get(patient_id, "cln_01")
        return await self.book_slot(
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            thread_id=thread_id,
            patient_id=patient_id,
            clinician_id=resolved_clinician,
            service=service,
            subtype=subtype,
            slot_start=resolved_slot,
            notes=notes,
            session=session,
        )

    async def run_turn(
        self,
        *,
        thread_id: str = "default_thread",
        patient_id: str,
        message: str,
        request_id: str | None = None,
        book_slot: dict[str, str] | None = None,
        classifier_fn=None,
        session: Any = None,
    ) -> dict[str, Any]:
        db, cleanup = await self._resolve_session(session)
        try:
            return await run_concierge_turn(
                db,
                thread_id=thread_id,
                patient_id=patient_id,
                message=message,
                request_id=request_id,
                book_slot=book_slot,
                classifier_fn=classifier_fn,
            )
        finally:
            await self._cleanup_session(cleanup)


__all__ = ["CareConciergeToolbox", "BookingConflictError"]