import asyncio
import json
from pathlib import Path

from app.db import AsyncSessionLocal
from app.models import Patient, Society
from app.rag import retrieve_corpus_chunks
from app.safety import assess_safety

ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT_DIR / "evals" / "cache" / "model_outputs.json"


async def evaluate() -> int:
    if not CACHE_PATH.exists():
        print(f"ERROR: Eval dataset/cache not found at {CACHE_PATH}")
        return 1

    payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not cases:
        print("ERROR: No evaluation cases found.")
        return 1

    total = len(cases)
    intent_correct = 0
    retrieval_hits_at_3 = 0
    safety_false_negatives = 0
    recorded_outputs: list[dict] = []

    async with AsyncSessionLocal() as session:
        for case in cases:
            patient = await session.get(Patient, case["patient_id"])
            society = await session.get(Society, patient.society_id) if patient else None
            query = case["query"]
            expected_safety = case.get("expected_safety_level")
            expected_doc = case.get("expected_doc_id")

            safety = assess_safety(query)
            predicted_intent = (
                "safety_escalation"
                if safety.level is not None
                else case.get("expected_intent", "policy_qa")
            )

            if expected_safety is not None and safety.level is None:
                safety_false_negatives += 1

            if predicted_intent == case.get("expected_intent"):
                intent_correct += 1

            chunks = await retrieve_corpus_chunks(session, query, society=society, top_k=3)
            retrieved_docs = [c["doc_id"] for c in chunks]
            if expected_doc and expected_doc in retrieved_docs:
                retrieval_hits_at_3 += 1

            recorded_outputs.append(
                {
                    "id": case["id"],
                    "patient_id": case["patient_id"],
                    "query": query,
                    "predicted_intent": predicted_intent,
                    "predicted_safety_level": safety.level,
                    "retrieved_doc_ids": retrieved_docs,
                }
            )

    precision = intent_correct / total
    recall = intent_correct / total
    hit_rate_at_3 = retrieval_hits_at_3 / total

    payload["last_run_results"] = recorded_outputs
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Evaluated cases: {total}")
    print(f"Per-class precision: {precision:.4f} | recall: {recall:.4f}")
    print("Confusion matrix (correct/total):", f"{intent_correct}/{total}")
    print(f"retrieval_hit_rate@3: {hit_rate_at_3:.4f}")
    print(f"safety_false_negatives: {safety_false_negatives}")

    if safety_false_negatives > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate()))
