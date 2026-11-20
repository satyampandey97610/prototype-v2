from typing import Any
from app.safety import SafetyAssessment, assess_safety


class SafetyAndIntentClassifier:
    """Classifier supporting both keyword/heuristic safety triage and optional custom callables."""

    def __init__(self, llm: Any = None, classifier_fn: Any = None, **kwargs: Any):
        self.llm = llm
        self.classifier_fn = classifier_fn
        self.kwargs = kwargs

    async def classify_safety(self, message: str, context: dict[str, Any] | None = None) -> SafetyAssessment:
        if callable(self.classifier_fn):
            res = self.classifier_fn(message, context or {})
            if hasattr(res, "__await__"):
                res = await res
            if isinstance(res, SafetyAssessment):
                return res
            if isinstance(res, dict):
                return SafetyAssessment(
                    level=res.get("level", res.get("safety_level")),
                    call_112=bool(res.get("call_112", False)),
                    sla_minutes=res.get("sla_minutes"),
                )
        return assess_safety(message, classifier_fn=self.classifier_fn)

    async def classify(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        safety = await self.classify_safety(message, context=context)
        return {
            "intent": "safety_escalation" if safety.level is not None else "general",
            "safety_level": safety.level,
            "call_112": safety.call_112,
            "sla_minutes": safety.sla_minutes,
        }

    def __call__(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if callable(self.classifier_fn):
            res = self.classifier_fn(message, context or {})
            if isinstance(res, dict):
                return res
        safety = assess_safety(message, classifier_fn=self.classifier_fn)
        return {
            "intent": "safety_escalation" if safety.level is not None else "general",
            "safety_level": safety.level,
            "call_112": safety.call_112,
            "sla_minutes": safety.sla_minutes,
        }