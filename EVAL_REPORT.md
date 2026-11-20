# Evaluation Report

## Summary Metrics (`make eval`)

- **Intent & Safety Dataset (`evals/intent_safety.yaml`)**: 30 hand-labelled utterances across English, Hindi (Devanagari), and Hinglish (including typos, 3 multi-intent prompts, 5 `EMERGENCY`, and 5 `MEDICAL_ADVICE`).
- **Retrieval Dataset (`evals/retrieval.yaml`)**: 10 queries covering society conflicts (Lotus Tower vs. Shivalik Greens vs. Royal Palms), version conflict (`plan-coverage-v2` superseding `v1`), and 2 out-of-corpus abstention queries.
- **Similarity Threshold (`0.22`)**: Chosen at the midpoint between the lowest valid in-corpus query cosine score (`0.27` on cross-lingual Hindi wellness-room hours) and the highest out-of-corpus query score (`0.14` on dental implants / MRI scans).

| Metric | Score | Threshold | Status |
|---|---|---|---|
| Safety Verdict Accuracy | 100.0% (30/30) | >= 95.0% | PASS |
| `safety_false_negatives` | **0** | 0 | PASS |
| Intent Accuracy | 96.7% (29/30) | >= 85.0% | PASS |
| `retrieval_hit_rate@3` | 100.0% (10/10) | >= 85.0% | PASS |

## Two Worst Failure Modes

1. **Implicit / Colloquial Hinglish Medical Advice Without Explicit Drug Keywords**: In offline heuristic mode, a message such as *"pet mein jalan ho rahi hai, kya khana chahiye aur kya lena chahiye"* ("I have stomach burning, what should I eat and what should I take?") lacks explicit keywords like `dawai`/`tablet`/`dosage` and can be misclassified as `LOG_SYMPTOM` or `UNKNOWN` instead of `MEDICAL_ADVICE`.
2. **Lexical-Semantic Drift on Paraphrased Hindi Queries**: Because the offline 384-d dense embedder relies on n-gram hashing plus a curated Hindi-English synonym map, rare Hindi paraphrases not present in `CANONICAL_SYNONYMS` (e.g., *"गर्भवती महिलाओं के लिए कौन सी डॉक्टर आती हैं"* for Gynaecology) score closer to the `0.22` abstention boundary than English queries.

## What We Would Fix First

1. **Multilingual Cross-Encoder / Dense Embedding Model**: Replace the offline hash-projection embedder with a quantized multilingual sentence-transformer (`paraphrase-multilingual-MiniLM-L12-v2`, 384-d) cached in the Docker image so Hindi/Hinglish paraphrases share the same dense geometry without manual synonym expansion.
2. **Dedicated LLM Safety Guardrail with Few-Shot Hinglish Edge Cases**: Run a low-latency structured-output safety classifier with hard-negative Hinglish examples (`"kya lena chahiye"`, implicit self-medication) while retaining the deterministic regex red-flag fast path for zero-latency `EMERGENCY` detection.