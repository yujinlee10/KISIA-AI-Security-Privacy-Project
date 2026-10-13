# Synthea 기반 의료 데이터를 읽어서 QI를 추출하는 파일
# 기본: user_patient_profiles_576.json (미리 처리된 파일)
# 노드 계층(노드_v0.9.csv)은 매칭/일반화에 여전히 필요하다.

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import DEFAULT_QI_ORDER, normalize_qi_value

# 모듈 수준 노드 레지스트리: node_id → {attribute, canonical_label, parent_id, depth}
NODE_REGISTRY: dict[str, dict[str, Any]] = {}

# 프로젝트 내 데이터 기본 경로
_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_NODE_PATH = _DATA_DIR / "노드_v0.9.csv"
DEFAULT_EVENT_PATH = _DATA_DIR / "사건연결표_v0.9.csv"
DEFAULT_PROFILES_PATH = _DATA_DIR / "user_patient_profiles_1000.json"

# event_type → QI 키 매핑 (CSV에서 직접 로드할 때 사용)
_EVENT_TYPE_TO_QI: dict[str, str] = {
    "CONDITION": "DX",
    "PROCEDURE": "PROC",
    "MEDICATION": "MED",
    "CAREPLAN": "PLAN",
}


@dataclass(frozen=True)
class PersonaRecord:
    person_id: str
    username: str | None
    qis: dict[str, Any]
    raw_profile: dict[str, Any]
    source_texts: list[str]


def load_node_registry(path: str | Path = DEFAULT_NODE_PATH) -> dict[str, dict[str, Any]]:
    """노드 테이블을 읽어 NODE_REGISTRY를 채우고 반환한다."""
    global NODE_REGISTRY
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"노드 파일을 찾을 수 없음: {path}")

    registry: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            node_id = row["node_id"].strip()
            registry[node_id] = {
                "attribute": row.get("attribute", "").strip(),
                "canonical_label": row.get("canonical_label", "").strip(),
                "parent_id": row.get("parent_id", "").strip() or None,
                "depth": int(row.get("depth") or 0),
            }

    NODE_REGISTRY = registry
    return registry


def load_population(
    profiles_path: str | Path = DEFAULT_PROFILES_PATH,
    node_path: str | Path = DEFAULT_NODE_PATH,
) -> list[PersonaRecord]:
    """미리 처리된 JSON에서 576명 환자 프로필을 로드한다.
    node_path의 노드 계층은 매칭/일반화에 필요하므로 항상 로드한다.
    """
    load_node_registry(node_path)

    profiles_path = Path(profiles_path)
    if not profiles_path.exists():
        raise FileNotFoundError(f"환자 프로필 파일을 찾을 수 없음: {profiles_path}")

    with profiles_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    return [
        PersonaRecord(
            person_id=entry["person_id"],
            username=None,
            qis=entry["qis"],
            raw_profile={},
            source_texts=[],
        )
        for entry in raw
    ]


def load_expanded_population(
    profiles_path: str | Path = DEFAULT_PROFILES_PATH,
    node_path: str | Path = DEFAULT_NODE_PATH,
    synth_path: str | Path | None = None,
) -> list[PersonaRecord]:
    """load_population의 별칭. 인터페이스 호환성 유지용."""
    return load_population(profiles_path, node_path)


def load_population_from_csv(
    event_path: str | Path = DEFAULT_EVENT_PATH,
    node_path: str | Path = DEFAULT_NODE_PATH,
) -> list[PersonaRecord]:
    """사건연결표 CSV를 직접 읽어 환자별 PersonaRecord를 생성한다.
    user_patient_profiles_576.json을 재생성할 때 사용한다.
    """
    load_node_registry(node_path)

    event_path = Path(event_path)
    if not event_path.exists():
        raise FileNotFoundError(f"사건연결표 파일을 찾을 수 없음: {event_path}")

    patient_events: dict[str, list[dict[str, str]]] = {}
    with event_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = row["patient_id"].strip()
            patient_events.setdefault(pid, []).append(dict(row))

    records: list[PersonaRecord] = []
    for pid, events in patient_events.items():
        qis = _aggregate_patient_qis(events)
        records.append(PersonaRecord(
            person_id=pid,
            username=None,
            qis=qis,
            raw_profile={},
            source_texts=[],
        ))

    return records


def _aggregate_patient_qis(events: list[dict[str, str]]) -> dict[str, Any]:
    """환자의 이벤트 목록에서 QI 딕셔너리를 추출한다."""
    sorted_events = sorted(events, key=lambda e: e.get("start") or "")

    first = sorted_events[0]
    raw_qis: dict[str, Any] = {
        "AGE": first.get("age_node") or None,
        "SEX": first.get("sex_node") or None,
        "REGION": first.get("region_node") or None,
    }

    seen_qi_types: set[str] = set()
    for event in sorted_events:
        etype = event.get("event_type", "").strip()
        qi_key = _EVENT_TYPE_TO_QI.get(etype)
        if qi_key and qi_key not in seen_qi_types:
            code_node = event.get("code_node") or None
            if code_node:
                raw_qis[qi_key] = code_node
                seen_qi_types.add(qi_key)
        if etype == "CONDITION" and "PERIOD" not in raw_qis:
            period = event.get("period_start_node") or None
            if period:
                raw_qis["PERIOD"] = period

    return {
        qi_type: normalize_qi_value(qi_type, raw_qis.get(qi_type))
        for qi_type in DEFAULT_QI_ORDER
        if normalize_qi_value(qi_type, raw_qis.get(qi_type)) is not None
    }
