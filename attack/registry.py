# Seed Registry 로더 (O1 New-QI Acquisition / O2 Specificity Refinement)
#
# 파일 형식이 둘이라 모두 받는다.
#   O1_seed_registry_v0.4.json : {"corpus_name", "corpus_version", ..., "seeds": [...]}
#   O2_seed_registry_v0.3_...  : [...]  (맨 리스트)
#
# 플레이스홀더 이름 통일 (팀 합의)
#   O2 원본은 [CURRENT_QI_STATE]를 쓰지만, 뜻은 "이전 응답에서 실제로 공개된 값"이다.
#   읽을 때 [PREVIOUSLY_DISCLOSED_QI]로 바꿔 내부에서는 새 이름만 쓴다. 원본 파일은 수정하지 않는다.
#
# 실험 단계 게이트 (D1 Stage 7)
#   현재 전 행이 human_reviewed=False, enabled_for_main=False다.
#   DEVELOPMENT 모드(POC·Pilot)에서는 쓸 수 있고, MAIN 모드에서는 사람 검토를 마친 행만 쓴다.
#   MAIN인데 쓸 수 있는 행이 없으면 오류로 멈춘다.

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from core.config import PROJECT_ROOT
from core.schemas import QI_TYPE_SET
from attack.conditions import Condition, parse_conditions


REGISTRY_DIR = PROJECT_ROOT / "data" / "seed_registry"
DEFAULT_REGISTRY_FILES = (
    REGISTRY_DIR / "O1_seed_registry_v0.4.json",
    REGISTRY_DIR / "O2_seed_registry_v0.3_row_level_3pass_audited.json",
)

PLACEHOLDER_SUBJECT = "[SUBJECT]"
PLACEHOLDER_DISCLOSED = "[PREVIOUSLY_DISCLOSED_QI]"
LEGACY_PLACEHOLDER_ALIASES = {"[CURRENT_QI_STATE]": PLACEHOLDER_DISCLOSED}
KNOWN_PLACEHOLDERS = frozenset({PLACEHOLDER_SUBJECT, PLACEHOLDER_DISCLOSED})

# 레지스트리의 attack_objective 이름 → evaluation 로그의 objective 코드 (log_schema_v1 VALID_OBJECTIVES)
OBJECTIVE_CODES = {"NEW_QI_ACQUISITION": "O1", "SPECIFICITY_REFINEMENT": "O2"}

_PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z_]+\]")


class SeedRegistryError(ValueError):
    """레지스트리 파일 형식·내용 오류. 어느 seed에서 났는지 함께 알려준다."""


class SeedGateError(RuntimeError):
    """MAIN 모드인데 사람 검토를 마친 Seed가 없을 때."""


class RunMode(str, Enum):
    DEVELOPMENT = "development"   # POC·Pilot: 검토 전 후보 Seed 사용 허용
    MAIN = "main"                 # 본실험: human_reviewed AND enabled_for_main 인 Seed만


@dataclass(frozen=True)
class Seed:
    """
    seed_id / seed_version / target_qi / scenario_id / expected_relation
    → 레지스트리 원본 값
    objective
    → "O1" 또는 "O2" (evaluation 로그 코드)
    template_text
    → 플레이스홀더 이름을 통일한 질문 템플릿
    conditions / condition_text / ontology_note
    → 해석한 조건, 원문, 원문 괄호 안 설명
    placeholders
    → 템플릿 본문에 실제로 있는 플레이스홀더
    declared_but_unused
    → allowed_placeholders에는 있는데 본문에 없는 것 (O1 S2 5행: [SUBJECT] 누락). 오류는 아니고 기록만
    human_reviewed / development_only / enabled_for_main
    → 검토·사용 단계 플래그
    source_file
    → 이 Seed를 읽은 파일 이름
    """
    seed_id: str
    seed_version: str
    target_qi: str
    objective: str
    scenario_id: str
    template_text: str
    conditions: tuple[Condition, ...]
    condition_text: str
    ontology_note: str | None
    placeholders: tuple[str, ...]
    declared_but_unused: tuple[str, ...]
    expected_relation: str
    human_reviewed: bool
    development_only: bool
    enabled_for_main: bool
    source_file: str

    @property
    def scenario_code(self) -> str:
        """'S1_DIRECT' → 'S1' (evaluation 로그 VALID_SCENARIOS 형식)."""
        return self.scenario_id.split("_", 1)[0]

    @property
    def needs_disclosed_value(self) -> bool:
        return PLACEHOLDER_DISCLOSED in self.placeholders


@dataclass(frozen=True)
class RegistrySource:
    """어떤 파일·버전으로 실험했는지 기록 (D1: Seed corpus hash/version)."""
    path: str
    sha256: str
    seed_count: int
    corpus_version: str | None


@dataclass(frozen=True)
class SeedRegistry:
    """읽기 전용 Seed 목록. 실행 중 바뀌지 않는다."""
    seeds: tuple[Seed, ...]
    mode: RunMode
    sources: tuple[RegistrySource, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.seeds)

    def by_id(self, seed_id: str) -> Seed:
        for seed in self.seeds:
            if seed.seed_id == seed_id:
                return seed
        raise KeyError(seed_id)

    def count_by(self, attribute: str) -> dict[str, int]:
        """요약용: objective·target_qi·scenario_code 등으로 개수 세기."""
        counts: dict[str, int] = {}
        for seed in self.seeds:
            key = getattr(seed, attribute)
            counts[key] = counts.get(key, 0) + 1
        return counts

    @classmethod
    def load(
        cls,
        paths: Iterable[str | Path] = DEFAULT_REGISTRY_FILES,
        *,
        mode: RunMode = RunMode.DEVELOPMENT,
    ) -> "SeedRegistry":
        """
        input : paths (레지스트리 JSON 파일들), mode (DEVELOPMENT / MAIN)
        output : SeedRegistry
        """
        seeds: list[Seed] = []
        sources: list[RegistrySource] = []
        seen: set[str] = set()

        for path in map(Path, paths):
            if not path.exists():
                raise FileNotFoundError(f"Seed Registry 파일이 없습니다: {path}")
            raw_bytes = path.read_bytes()
            rows, corpus_version = _rows_of(json.loads(raw_bytes.decode("utf-8")), path)

            for row in rows:
                seed = _parse_seed(row, path.name)
                if seed.seed_id in seen:
                    raise SeedRegistryError(f"중복 seed_id: {seed.seed_id} ({path.name})")
                seen.add(seed.seed_id)
                seeds.append(seed)

            sources.append(RegistrySource(str(path), hashlib.sha256(raw_bytes).hexdigest(),
                                          len(rows), corpus_version))

        if mode is RunMode.MAIN:
            seeds = [s for s in seeds if s.human_reviewed and s.enabled_for_main]
            if not seeds:
                raise SeedGateError(
                    "MAIN 모드에서 쓸 수 있는 Seed가 없습니다 (human_reviewed·enabled_for_main 모두 True인 행 0개). "
                    "사람 검토와 v1.0 freeze 전에는 DEVELOPMENT 모드로만 실행할 수 있습니다."
                )

        return cls(tuple(seeds), mode, tuple(sources))


# ──────────────────────────────────────────────────────────────
# 내부 함수
# ──────────────────────────────────────────────────────────────

def _rows_of(body: Any, path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """O1(래퍼 dict)·O2(맨 리스트) 두 형식을 모두 받는다."""
    if isinstance(body, list):
        return body, None
    if isinstance(body, dict) and isinstance(body.get("seeds"), list):
        return body["seeds"], body.get("corpus_version")
    raise SeedRegistryError(f"알 수 없는 레지스트리 형식: {path.name}")


def _rename_placeholders(text: str) -> str:
    for legacy, current in LEGACY_PLACEHOLDER_ALIASES.items():
        text = text.replace(legacy, current)
    return text


def _parse_seed(row: dict[str, Any], source: str) -> Seed:
    seed_id = row.get("seed_id")
    if not isinstance(seed_id, str) or not seed_id:
        raise SeedRegistryError(f"seed_id가 없는 행이 있습니다 ({source})")

    def need(name: str) -> Any:
        value = row.get(name)
        if value in (None, ""):
            raise SeedRegistryError(f"{seed_id}: '{name}' 필드가 비어 있습니다.")
        return value

    target_qi = need("target_qi")
    if target_qi not in QI_TYPE_SET:
        raise SeedRegistryError(f"{seed_id}: 알 수 없는 target_qi {target_qi!r}")

    objective_name = need("attack_objective")
    if objective_name not in OBJECTIVE_CODES:
        raise SeedRegistryError(f"{seed_id}: 알 수 없는 attack_objective {objective_name!r}")

    template = _rename_placeholders(need("template_text"))
    declared = {
        _rename_placeholders(p.strip())
        for p in str(row.get("allowed_placeholders") or "").split(";") if p.strip()
    }
    used = tuple(dict.fromkeys(_PLACEHOLDER_PATTERN.findall(template)))

    unknown = [p for p in (*used, *declared) if p not in KNOWN_PLACEHOLDERS]
    if unknown:
        raise SeedRegistryError(f"{seed_id}: 알 수 없는 플레이스홀더 {unknown}")
    undeclared = [p for p in used if p not in declared]
    if undeclared:
        raise SeedRegistryError(f"{seed_id}: 선언되지 않은 플레이스홀더 사용 {undeclared}")

    condition_text = need("required_state_condition")
    try:
        conditions, note = parse_conditions(condition_text)
    except ValueError as exc:
        raise SeedRegistryError(f"{seed_id}: {exc}") from None

    return Seed(
        seed_id=seed_id,
        seed_version=str(row.get("seed_version") or ""),
        target_qi=target_qi,
        objective=OBJECTIVE_CODES[objective_name],
        scenario_id=need("scenario_id"),
        template_text=template,
        conditions=conditions,
        condition_text=condition_text,
        ontology_note=note,
        placeholders=used,
        declared_but_unused=tuple(sorted(declared - set(used))),
        expected_relation=str(row.get("expected_relation") or ""),
        human_reviewed=bool(row.get("human_reviewed")),
        development_only=bool(row.get("development_only")),
        enabled_for_main=bool(row.get("enabled_for_main")),
        source_file=source,
    )
