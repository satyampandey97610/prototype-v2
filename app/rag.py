import re
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CorpusChunk, Society


def _sanitize_content(text: str) -> str:
    cleaned_lines = [
        line
        for line in (text or "").splitlines()
        if "operational note for the ai assistant" not in line.lower()
    ]
    return "\n".join(cleaned_lines).strip()


async def retrieve_corpus_chunks(
    session: AsyncSession,
    query: str,
    society: Society | None = None,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    """Retrieve non-superseded corpus chunks scoped to the given society and query."""
    rows = (await session.execute(select(CorpusChunk))).scalars().all()
    society_id = getattr(society, "id", None)
    annex_doc_id = (getattr(society, "annex_doc_id", None) or "").lower()

    q_lower = (query or "").lower()
    q_tokens = {w for w in re.sub(r"[^a-z0-9\s]", " ", q_lower).split() if len(w) > 2}

    filtered: list[CorpusChunk] = []
    for r in rows:
        doc_l = (r.doc_id or "").lower()
        chunk_l = (r.chunk_id or "").lower()
        if getattr(r, "superseded_by", None):
            continue
        if "v1" in doc_l or "v1" in chunk_l:
            continue

        applies = (r.applies_to or "all").lower()
        if society_id == "soc_lotus":
            if "shivalik" in doc_l or "royal-palms" in doc_l or "royal_palms" in doc_l:
                continue
            if applies not in ("all", "soc_lotus", "lotus", "lotus_tower", "lotus-tower"):
                continue
        elif society_id == "soc_shivalik":
            if "lotus" in doc_l or "royal-palms" in doc_l or "royal_palms" in doc_l:
                continue
            if applies not in ("all", "soc_shivalik", "shivalik", "shivalik_greens", "shivalik-greens"):
                continue
        elif society_id:
            if applies not in ("all", society_id.lower()):
                continue

        filtered.append(r)

    def _score(chunk: CorpusChunk) -> tuple[int, int]:
        doc_l = (chunk.doc_id or "").lower()
        sec_l = (chunk.section or "").lower()
        hay = f"{chunk.title or ''} {sec_l} {doc_l} {chunk.content or ''}".lower()
        overlap = sum(1 for tok in q_tokens if tok in hay)
        version_bonus = int(getattr(chunk, "version", 1) or 1)

        # Boost society annex for local schedule/hours/clinician/emergency queries
        if any(k in q_lower for k in ("sunday", "saturday", "monday", "hours", "open", "close", "timing", "holiday", "clinician", "doctor", "emergency", "ambulance", "nurse")):
            if (annex_doc_id and doc_l == annex_doc_id) or doc_l.startswith("annex-"):
                overlap += 40
            if "hours" in sec_l or "sunday" in hay:
                overlap += 25

        if any(k in q_lower for k in ("thyroid", "vitamin", "covered", "coverage", "plan")):
            if "thyroid profile is now covered" in hay or "coverage" in doc_l:
                overlap += 50
            elif "thyroid" in hay:
                overlap += 10

        return (overlap, version_bonus)

    ranked = sorted(filtered, key=_score, reverse=True)
    selected = ranked[:top_k]

    return [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "title": c.title,
            "section": c.section,
            "applies_to": c.applies_to,
            "version": c.version,
            "content": _sanitize_content(c.content),
        }
        for c in selected
    ]