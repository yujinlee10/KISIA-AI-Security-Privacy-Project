# Privacy State와 QI 데이터 구조를 정의하는 파일
# 현재까지 어떤 QI가 쌓였는지 관리하는 파일

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DEFAULT_QI_ORDER = [
    "age",
    "location",
    "occupation",
    "education",
    "relationship_status",
]

DIRECT_IDENTIFIER_FIELDS = {"author", "username", "person_id"}
QIStatus = Literal["committed", "pending", "conflict", "rejected"]

@dataclass(frozen=True)
class QIObservation:
    qi_type: str
    value: Any
    source_turn: int | None = None
    confidence: float | None = None
    source: str | None = None


@dataclass
class PrivacyState:
    target_id: str
    session_id: str
    turn: int = 0
    qis: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)

    def committed_qis(self) -> dict[str, Any]:
        return {
            qi_type: entry["value"]
            for qi_type, entry in self.qis.items()
            if entry.get("status") == "committed"
        }

    def copy(self) -> "PrivacyState":
        return PrivacyState(
            target_id=self.target_id,
            session_id=self.session_id,
            turn=self.turn,
            qis={key: dict(value) for key, value in self.qis.items()},
            conflicts=[dict(value) for value in self.conflicts],
            pending=[dict(value) for value in self.pending],
        )


def update_state(
    state: PrivacyState,
    observations: list[QIObservation],
    *,
    min_confidence: float = 0.5,
) -> PrivacyState:
    next_state = state.copy()

    for observation in observations:
        qi_type = observation.qi_type
        normalized = normalize_qi_value(qi_type, observation.value)
        event = {
            "qi_type": qi_type,
            "value": normalized,
            "source_turn": observation.source_turn,
            "confidence": observation.confidence,
            "source": observation.source,
        }

        if qi_type not in DEFAULT_QI_ORDER or normalized in (None, ""):
            event["reason"] = "unsupported_or_empty_qi"
            next_state.pending.append(event)
            continue

        if observation.confidence is not None and observation.confidence < min_confidence:
            event["reason"] = "low_confidence"
            next_state.pending.append(event)
            continue

        existing = next_state.qis.get(qi_type)
        if existing is None:
            next_state.qis[qi_type] = {
                **event,
                "status": "committed",
                "history": [event],
            }
            continue

        existing_value = existing.get("value")
        if existing_value == normalized:
            existing.setdefault("history", []).append({**event, "reason": "same_value"})
            continue

        if is_more_specific(qi_type, normalized, existing_value):
            existing.setdefault("history", []).append(
                {**event, "reason": "more_specific_update", "previous_value": existing_value}
            )
            existing.update(event)
            existing["status"] = "committed"
            continue

        if is_more_specific(qi_type, existing_value, normalized):
            existing.setdefault("history", []).append(
                {**event, "reason": "less_specific_ignored", "kept_value": existing_value}
            )
            continue

        next_state.conflicts.append(
            {
                **event,
                "reason": "conflicting_value",
                "existing_value": existing_value,
            }
        )

    return next_state


def normalize_qi_value(qi_type: str, value: Any) -> Any:
    if value is None:
        return None

    if qi_type == "age":
        return to_age_band(value)

    if isinstance(value, str):
        normalized = " ".join(value.strip().split())
        return normalized if normalized else None

    return value


def to_age_band(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            return None
        if raw.endswith("s") and raw[:-1].isdigit():
            return raw
        if raw in {"teens", "young", "adult", "senior"}:
            return raw
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return raw
        value = int(digits)

    if not isinstance(value, int):
        return str(value)

    if value < 20:
        return "teens"
    decade = value // 10 * 10
    return f"{decade}s"


def is_more_specific(qi_type: str, candidate: Any, existing: Any) -> bool:
    if candidate in (None, "") or existing in (None, ""):
        return False

    if qi_type == "location":
        return _location_specificity(str(candidate)) > _location_specificity(str(existing))

    if qi_type == "age":
        return _age_specificity(candidate) > _age_specificity(existing)

    if qi_type == "education":
        return _education_specificity(str(candidate)) > _education_specificity(str(existing))

    return False


def _location_specificity(value: str) -> int:
    return len([part for part in value.split(",") if part.strip()])


def _age_specificity(value: Any) -> int:
    text = str(value)
    return 2 if text.isdigit() else 1


def _education_specificity(value: str) -> int:
    lowered = value.lower()
    if any(token in lowered for token in ["computer", "business", "marketing", "physics"]):
        return 2
    return 1
