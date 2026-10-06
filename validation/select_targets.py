# 실험 대상 고르기: python -m validation.select_targets --count 10
#
# SynthPAI의 300명 가운데 누구를 실험 대상으로 삼을지 정한다. 대상마다 "글만 읽고 어디까지 알아낼 수 있는가"를
# SynthPAI에 들어 있는 사람 라벨(reviews.human: 글 하나를 읽고 사람이 적은 추정값)로 어림하고,
# 단서가 충분한 사람과 부족한 사람을 정해진 비율로 섞어 뽑는다.
#
# 왜 섞는가
#   단서가 충분한 사람만 고르면 "Defender가 없으면 뚫린다"는 결과가 부풀려지고,
#   부족한 사람만 고르면 Defender가 개입할 일이 없어 비교가 되지 않는다. 두 층의 결과를 따로 보고한다.
#
# 어림하는 방법 (대상마다)
#   1) 그 사람의 글에 달린 사람 추정값을 QI별로 모은다
#   2) 추정값을 온톨로지 node로 읽고, 실제 값과 같거나 더 넓은 것(맞는 추정)만 남긴다
#   3) QI마다 가장 구체적인 맞는 추정을 골라, 그 조건을 모두 만족하는 기준 집단 인원(k_reachable)을 센다
#   k_reachable < k_req 이면 "단서 충분(rich)", 아니면 "단서 부족(sparse)" 층이다.
#
# 한계
#   사람 라벨은 글 하나만 보고 적은 것이라, 여러 글을 종합하는 Target LLM이 실제로 내놓는 답과 다를 수 있다.
#   k_reachable은 층을 나누는 기준일 뿐 결과 수치가 아니다.
#
# 정보 경계
#   이 도구는 정답 profile을 읽는다. 실험 설계 단계에서 한 번 쓰는 것이고, 실행 중 코드는 이 파일을 import하지 않는다.
#   결과 파일에는 대상 ID·층·k_reachable·라벨이 있는 QI 목록만 적고 정답 값은 적지 않는다.
#   Fuzzer와 Defender에는 대상 ID만 넘어간다.

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from adapters.ontology_risk import build_population_index
from adapters.risk_adapter import PopulationMode, RiskConfig, load_reference_population
from core.config import PROJECT_ROOT
from evaluation_v1.log_schema_v1 import KST
from ontology.builder import PROFILE_FIELD_BY_QI
from ontology.index import PopulationIndex
from ontology.mapper import ProfileMapper
from target_llm.corpus import DEFAULT_COMMENTS_PATH

DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "target_selection.json"
RICH, SPARSE = "rich", "sparse"


@dataclass(frozen=True)
class Candidate:
    """
    target_id       → SynthPAI author ID
    comments        → 글 수
    labelled_qis    → 사람 라벨이 실제 값과 맞게 달린 QI
    k_reachable     → 그 라벨들을 모두 알았을 때의 후보 수 (기준 집단 기준)
    stratum         → rich (k_reachable < k_req) / sparse
    """
    target_id: str
    comments: int
    labelled_qis: tuple[str, ...]
    k_reachable: int
    stratum: str

    def to_log_dict(self) -> dict[str, Any]:
        return {**vars(self), "labelled_qis": list(self.labelled_qis)}


def assess(comments_path: str | Path, index: PopulationIndex, k_req: int) -> list[Candidate]:
    """
    input : comments_path (synthpai.jsonl), index (기준 집단 색인), k_req
    output : 대상 후보 전체 (파일에 나온 순서)
    """
    ontology, parser = index.ontology, index.parser
    mapper = ProfileMapper(ontology, parser)
    field_to_qi = {field: qi for qi, field in PROFILE_FIELD_BY_QI.items()}

    profiles: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    estimates: dict[str, dict[str, list[str]]] = {}
    for line in Path(comments_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        author = row["author"]
        profiles.setdefault(author, row.get("profile") or {})
        counts[author] = counts.get(author, 0) + 1
        for review in (row.get("reviews") or {}).values():
            for field, qi in field_to_qi.items():
                value = review.get(field) if isinstance(review, dict) else None
                text = str(value.get("estimate", "")).strip() if isinstance(value, dict) else ""
                if text:
                    estimates.setdefault(author, {}).setdefault(qi, []).append(text)

    candidates: list[Candidate] = []
    for author, profile in profiles.items():
        truth = mapper.map_profile(profile)
        known: dict[str, str] = {}
        for qi, texts in estimates.get(author, {}).items():
            actual = truth.get(qi)
            if actual is None:
                continue
            valid = [node for node in (parser.parse(qi, text).node_id for text in texts)
                     if node is not None and ontology.is_same_or_descendant(actual, node)]
            valid = [node for node in valid if not ontology.node(node).is_root]
            if valid:
                known[qi] = max(valid, key=ontology.depth)
        k = index.count(known)
        candidates.append(Candidate(author, counts[author], tuple(sorted(known)), k, RICH if k < k_req else SPARSE))
    return candidates


def select(candidates: Sequence[Candidate], count: int, *, rich_share: float, seed: int,
           include: Sequence[str] = ()) -> list[Candidate]:
    """
    input : candidates, count (뽑을 인원), rich_share (단서 충분 층의 비율 0~1), seed (난수),
            include (반드시 넣을 대상 ID. 층별 정원에 포함해 센다)
    output : 뽑힌 대상 (rich 먼저, 그 안에서는 ID 순)

    한 층의 인원이 모자라면 다른 층에서 채운다.
    """
    if not 0 <= rich_share <= 1:
        raise ValueError("rich_share는 0과 1 사이여야 합니다.")
    by_id = {candidate.target_id: candidate for candidate in candidates}
    missing = [target_id for target_id in include if target_id not in by_id]
    if missing:
        raise ValueError(f"데이터에 없는 대상: {missing}")
    if count < len(set(include)) or count > len(candidates):
        raise ValueError(f"count는 {len(set(include))} 이상 {len(candidates)} 이하여야 합니다.")

    rng = random.Random(seed)
    chosen = [by_id[target_id] for target_id in dict.fromkeys(include)]
    quota = {RICH: round(count * rich_share)}
    quota[SPARSE] = count - quota[RICH]
    for stratum in (RICH, SPARSE):
        need = quota[stratum] - sum(1 for c in chosen if c.stratum == stratum)
        pool = sorted((c for c in candidates if c.stratum == stratum and c not in chosen), key=lambda c: c.target_id)
        chosen.extend(rng.sample(pool, max(0, min(need, len(pool)))))
    if len(chosen) < count:      # 한 층이 모자랐을 때
        pool = sorted((c for c in candidates if c not in chosen), key=lambda c: c.target_id)
        chosen.extend(rng.sample(pool, count - len(chosen)))
    return sorted(chosen[:count], key=lambda c: (c.stratum != RICH, _natural(c.target_id)))


def _natural(target_id: str) -> tuple[str, int]:
    digits = "".join(ch for ch in target_id if ch.isdigit())
    return target_id.rstrip("0123456789"), int(digits or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="실험 대상을 층화해서 고른다 (LLM 호출 없음)")
    parser.add_argument("--count", type=int, default=10, help="뽑을 대상 수")
    parser.add_argument("--rich-share", type=float, default=0.7,
                        help="글에 단서가 충분한 층의 비율 (기본 0.7). 나머지는 단서가 부족한 층에서 뽑는다")
    parser.add_argument("--seed", type=int, default=42, help="추첨 난수")
    parser.add_argument("--include", nargs="*", default=[], help="반드시 넣을 대상 ID (예: pers1)")
    parser.add_argument("--threshold", type=int, default=RiskConfig().risk_threshold, help="위험 기준 (k_req = 값 + 1)")
    parser.add_argument("--population", choices=[mode.value for mode in PopulationMode], default="expanded")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="선정 결과를 적을 파일")
    args = parser.parse_args()

    k_req = RiskConfig(risk_threshold=args.threshold).k_req
    population = load_reference_population(PopulationMode(args.population))
    candidates = assess(DEFAULT_COMMENTS_PATH, build_population_index(population), k_req)
    rich = sum(1 for c in candidates if c.stratum == RICH)
    chosen = select(candidates, args.count, rich_share=args.rich_share, seed=args.seed, include=args.include)

    print(f"------ 후보 {len(candidates)}명: 단서 충분 {rich}명 / 부족 {len(candidates) - rich}명 "
          f"(기준 집단 {population.size:,}명, k_req {k_req}) ------")
    print(f"{'대상':<10}{'층':<8}{'글 수':>5}  {'k_reachable':>11}  라벨이 맞게 달린 QI")
    for c in chosen:
        print(f"{c.target_id:<10}{c.stratum:<8}{c.comments:>5}  {c.k_reachable:>11,}  {', '.join(c.labelled_qis) or '-'}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "created_at": datetime.now(KST).isoformat(),
        "criteria": {"count": args.count, "rich_share": args.rich_share, "seed": args.seed, "include": args.include,
                     "k_req": k_req, "population": {"mode": population.mode.value, "size": population.size},
                     "assessment_fingerprint": build_hash(candidates, population.size),
                     "method": "SynthPAI reviews.human 추정값 중 실제 값과 같거나 넓은 것으로 센 후보 수 (k_reachable)"},
        "pool": {"total": len(candidates), RICH: rich, SPARSE: len(candidates) - rich},
        "target_ids": [c.target_id for c in chosen],
        "targets": [c.to_log_dict() for c in chosen],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {output}")
    print("실행: python -m orchestrator --targets-file " + _shown(output)
          + " --arms C0 C1 C1S --backend ontology --turns 12 --paired --experiment-id run_multi10")


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def build_hash(candidates: Sequence[Candidate], population_size: int) -> str:
    """같은 데이터·같은 기준으로 평가했는지 확인하기 위한 짧은 지문."""
    import hashlib

    text = json.dumps([[c.target_id, c.k_reachable] for c in candidates] + [population_size])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    main()
