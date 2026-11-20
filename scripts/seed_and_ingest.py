import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import Clinician, CorpusChunk, Patient, Society

ROOT_DIR = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT_DIR / "data" / "seed"
CORPUS_DIR = ROOT_DIR / "data" / "corpus"


def parse_frontmatter_and_sections(md_text: str, doc_id: str) -> list[dict]:
    meta: dict = {
        "doc_id": doc_id,
        "title": doc_id,
        "applies_to": "all",
        "version": 1,
        "effective_from": None,
        "superseded_by": None,
    }
    body = md_text

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", md_text, flags=re.DOTALL)
    if fm_match:
        fm_block, body = fm_match.group(1), fm_match.group(2)
        for line in fm_block.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val.lower() == "null" or val == "":
                    val = None
                elif key == "version":
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                meta[key] = val

    parts = re.split(r"(?m)^(#{1,3}\s+.+)$", body.strip())
    chunks: list[dict] = []
    current_heading = meta["title"]
    buffer: list[str] = []

    def flush_chunk(idx: int, heading: str, content: str) -> dict | None:
        cleaned = content.strip()
        if not cleaned:
            return None
        return {
            "chunk_id": f"{meta['doc_id']}#chunk-{idx}",
            "doc_id": meta["doc_id"],
            "title": meta["title"],
            "section": heading.lstrip("#").strip(),
            "applies_to": meta["applies_to"],
            "version": meta["version"],
            "effective_from": meta["effective_from"],
            "superseded_by": meta["superseded_by"],
            "content": cleaned,
        }

    idx = 0
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,3}\s+", stripped):
            flushed = flush_chunk(idx, current_heading, "\n".join(buffer))
            if flushed:
                chunks.append(flushed)
                idx += 1
            current_heading = stripped
            buffer = [stripped]
        else:
            buffer.append(stripped)

    flushed = flush_chunk(idx, current_heading, "\n".join(buffer))
    if flushed:
        chunks.append(flushed)

    # Deduplicate by chunk_id while preserving order (merge content if duplicate chunk_id occurs)
    deduped: dict[str, dict] = {}
    for ch in chunks:
        cid = ch["chunk_id"]
        if cid in deduped:
            deduped[cid]["content"] = f"{deduped[cid]['content']}\n\n{ch['content']}".strip()
        else:
            deduped[cid] = ch

    return list(deduped.values())


def deterministic_embedding(text: str, dim: int = 1536) -> list[float]:
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    if not tokens:
        return vec
    for i, tok in enumerate(tokens):
        digest = hashlib.sha256(f"{tok}:{i}".encode("utf-8")).digest()
        for j in range(0, len(digest), 2):
            idx = int.from_bytes(digest[j : j + 2], "big") % dim
            sign = 1.0 if (digest[j] % 2 == 0) else -1.0
            vec[idx] += sign

    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


async def seed_all() -> None:
    async with AsyncSessionLocal() as session:
        societies_path = SEED_DIR / "societies.json"
        if societies_path.exists():
            for item in json.loads(societies_path.read_text(encoding="utf-8")):
                existing = await session.get(Society, item["id"])
                if existing:
                    for k, v in item.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                else:
                    session.add(Society(**item))

        clinicians_path = SEED_DIR / "clinicians.json"
        if clinicians_path.exists():
            for item in json.loads(clinicians_path.read_text(encoding="utf-8")):
                existing = await session.get(Clinician, item["id"])
                if existing:
                    for k, v in item.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                else:
                    session.add(Clinician(**item))

        patients_path = SEED_DIR / "patients.json"
        if patients_path.exists():
            for item in json.loads(patients_path.read_text(encoding="utf-8")):
                existing = await session.get(Patient, item["id"])
                if existing:
                    for k, v in item.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                else:
                    session.add(Patient(**item))

        await session.flush()

        if CORPUS_DIR.exists():
            seen_chunk_ids: set[str] = set()
            for md_file in sorted(CORPUS_DIR.glob("*.md")):
                doc_id = md_file.stem
                raw_text = md_file.read_text(encoding="utf-8")
                for ch in parse_frontmatter_and_sections(raw_text, doc_id):
                    if ch["chunk_id"] in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(ch["chunk_id"])

                    emb = deterministic_embedding(f"{ch['title']} {ch['section']} {ch['content']}")
                    existing_chunk = (
                        await session.execute(select(CorpusChunk).where(CorpusChunk.chunk_id == ch["chunk_id"]))
                    ).scalar_one_or_none()
                    if existing_chunk:
                        existing_chunk.content = ch["content"]
                        existing_chunk.section = ch["section"]
                        existing_chunk.version = ch["version"]
                        existing_chunk.superseded_by = ch["superseded_by"]
                        existing_chunk.applies_to = ch["applies_to"]
                        existing_chunk.embedding = emb
                    else:
                        payload = {k: v for k, v in ch.items() if hasattr(CorpusChunk, k)}
                        payload["embedding"] = emb
                        session.add(CorpusChunk(**payload))

        await session.commit()
        print("Seeded societies, clinicians, patients, and corpus chunks successfully.")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONPATH", str(ROOT_DIR))
    asyncio.run(seed_all())
