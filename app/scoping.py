from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, Clinician, CorpusChunk, Society


async def list_scoped_service_requests(
    session: AsyncSession,
    *,
    patient_id: str | None = None,
    society_id: str | None = None,
) -> list[Booking]:
    stmt = select(Booking)
    if patient_id is not None:
        stmt = stmt.where(Booking.patient_id == patient_id)
    if society_id is not None:
        stmt = stmt.where(Booking.society_id == society_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


def filter_scoped_chunks(
    chunks: list[CorpusChunk],
    society: Society | None,
) -> list[CorpusChunk]:
    allowed_annex = society.annex_doc_id if society else None
    soc_id = society.id if society else None
    scoped: list[CorpusChunk] = []
    for ch in chunks:
        if ch.superseded_by:
            continue
        if ch.doc_id.startswith("annex-"):
            if allowed_annex and ch.doc_id != allowed_annex:
                continue
            if ch.applies_to not in ("all", soc_id, allowed_annex):
                continue
        elif ch.applies_to not in ("all", None):
            if soc_id and ch.applies_to != soc_id:
                continue
        scoped.append(ch)
    return scoped


def list_scoped_clinicians(
    clinicians: list[Clinician],
    society: Society | None,
) -> list[Clinician]:
    if society is None or not isinstance(society.hospital, dict):
        return list(clinicians)
    hospital_name = (society.hospital.get("name") or "").lower()
    if not hospital_name:
        return list(clinicians)
    return [c for c in clinicians if hospital_name in (c.hospital or "").lower()]