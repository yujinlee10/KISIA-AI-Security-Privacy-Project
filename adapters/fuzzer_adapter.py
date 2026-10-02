# 한재이 파트(fuzzer) 연결 어댑터: 다음 턴 공격 질문을 만든다
#
# 한재이 파트에서 그대로 가져다 쓰는 것 (수정 없음)
#   fuzzer.feedback.evaluate_feedback          : k 변화 → FuzzerFeedback(status, score, accepted)
#   fuzzer.feedback.select_mutation_strategy   : FuzzerFeedback → 전략(intensify/refine/diversify/revert/exploit/abort)
#   fuzzer.fuzzer_mutation.generate_next_mutation : (직전 질문, 전략) → Mutation 지시문
#   fuzzer/seedinput/seedprompt.txt            : Mutation LLM system prompt
#
# fuzzer.llm_client.llm_request는 쓰지 않고 같은 계약을 core.llm_client로 실행한다.
#   llm_request는 import하는 순간 fuzzer/.env를 읽고 출력을 내며, 모델 설정도 따로 갖는다.
#   system prompt 파일(seedprompt.txt)·max_tokens(512)는 같게 맞춰 결과가 달라지지 않게 했다.
#   이렇게 하면 Fuzzer·Target·Extractor가 같은 .env·같은 모델을 쓴다.
#
# 한 턴의 질문 만드는 규칙 (POC 기본 정책 — Fuzzer 담당 확인 필요)
#   새 Seed로 시작 : 첫 턴 / 직전 전략이 abort / 지금 Seed가 State상 더 이상 조건에 맞지 않음
#                   / k가 변하지 않은 턴이 max_stale_turns번 이어짐 (같은 Seed에 턴을 다 쓰는 것 방지)
#                   → 조건에 맞는 Seed를 균등하게 골라 채운 뒤, 한재이 main.py의 Turn 0처럼 그대로 Mutation LLM에 보냄
#   이어서 변형     : 그 외 → 한재이 main.py 루프처럼 generate_next_mutation(직전 질문, 전략) → Mutation LLM
#
# 정보 경계
#   k는 evaluate_feedback 안에서만 쓰인다. Mutation LLM에는 전략 이름·지시문·직전 질문만 간다 (D1 Stage 6).

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from attack.selector import AttackStateView, SeedSelector
from attack.registry import Seed
from core.config import PROJECT_ROOT
from core.llm_client import LLMResponse
from fuzzer.feedback import FuzzerFeedback, evaluate_feedback, select_mutation_strategy
from fuzzer.fuzzer_mutation import generate_next_mutation


FUZZER_SYSTEM_PROMPT_FILE = PROJECT_ROOT / "fuzzer" / "seedinput" / "seedprompt.txt"

# fuzzer/llm_client.py의 max_tokens와 같은 값 (변형 질문은 짧다)
MUTATION_MAX_TOKENS = 512

ABORT_STRATEGY = "abort"

_UNRESOLVED_PLACEHOLDER = re.compile(r"\[[A-Z_]{3,}\]")


class NoApplicableSeedError(RuntimeError):
    """지금 State에 맞는 Seed가 하나도 없을 때 (D1 종료 사유 NO_APPLICABLE_ACTION)."""


class CompletionClient(Protocol):
    def complete(self, user_prompt: str, *, system_prompt: str | None = None,
                 max_tokens: int | None = None, temperature: float | None = None) -> LLMResponse:
        ...


# ──────────────────────────────────────────────────────────────
# 설정 · 결과
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FuzzerConfig:
    """
    risk_threshold
    → evaluate_feedback의 threshold. risk 어댑터(RiskConfig)와 같은 값이어야 한다
      (한재이 main.py는 5로 고정 호출 → 통합에서는 공통 값 하나로 맞춤)
    max_mutation_retries
    → 변형 결과가 유효하지 않을 때 다시 요청하는 횟수 (첫 요청 제외). D1: Pilot 후 freeze
    max_query_chars
    → 이보다 긴 변형 결과는 무효 처리
    max_stale_turns
    → k가 변하지 않은(unchanged) 턴이 이만큼 이어지면 새 Seed로 넘어간다. None이면 넘어가지 않는다.
      Target이 답할 수 없는 QI를 노리는 Seed에 남은 턴을 모두 쓰는 것을 막는다. 값은 Fuzzer 담당 확인 필요
    """
    risk_threshold: int = 3
    max_mutation_retries: int = 1
    max_query_chars: int = 1000
    max_stale_turns: int | None = 2


@dataclass(frozen=True)
class AttackStep:
    """
    한 턴의 공격 질문과 그것이 만들어진 과정 (trusted 로그용).

    query
    → Target에 실제로 보낼 질문
    seed_id / objective / scenario_code / target_qi
    → 이 질문의 출발 Seed (evaluation 로그의 seed_id·objective·scenario_id·target_qi)
    reset_reason
    → 새 Seed로 시작한 이유 (FIRST_TURN / ABORT / SEED_NOT_APPLICABLE / NO_FEEDBACK / STALE). 이어서 변형했으면 None
    strategy / feedback_status
    → 한재이 feedback이 고른 전략과 판정 (새 Seed로 시작한 턴이면 전략은 None)
    bound_seed_text / mutation_instruction
    → 채워진 Seed 문장 / generate_next_mutation 지시문 (이어서 변형한 턴만)
    mutation_attempts / mutation_valid / invalid_reasons / fallback_used
    → 변형 시도 수, 최종 유효 여부, 무효 사유, 변형 실패로 채워진 Seed 원문을 그대로 쓴 경우
    applicable_seed_count
    → 이번 턴에 조건을 만족한 Seed 수
    mutation_input_tokens / mutation_output_tokens / mutation_latency_ms
    → Mutation LLM 비용 (D1: Target 호출과 따로 집계)
    """
    turn: int
    query: str
    seed_id: str
    seed_version: str
    objective: str
    scenario_code: str
    target_qi: str
    reset_reason: str | None
    strategy: str | None
    feedback_status: str | None
    bound_seed_text: str
    mutation_instruction: str | None
    mutation_attempts: int
    mutation_valid: bool
    invalid_reasons: tuple[str, ...]
    fallback_used: bool
    applicable_seed_count: int
    mutation_input_tokens: int
    mutation_output_tokens: int
    mutation_latency_ms: float

    def to_log_dict(self) -> dict[str, Any]:
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in vars(self).items()}


# ──────────────────────────────────────────────────────────────
# 어댑터
# ──────────────────────────────────────────────────────────────

class FuzzerAdapter:
    """
    세션 하나의 공격 진행을 담당한다.

    사용 순서 (orchestrator가 매 턴 호출)
        step = adapter.next_query(view)          # 질문 만들기
        ... Target 응답 → 공개 → 추출 → commit ...
        adapter.observe(k_before, k_after)       # 그 턴의 k 변화를 알려주기
    """

    def __init__(self, selector: SeedSelector, client: CompletionClient,
                 config: FuzzerConfig = FuzzerConfig()) -> None:
        self._selector = selector
        self._client = client
        self._config = config
        self._system_prompt = _load_system_prompt()
        self._turn = 0
        self._current_seed: Seed | None = None
        self._current_bound_text: str | None = None
        self._current_query: str | None = None
        self._last_feedback: FuzzerFeedback | None = None
        self._stale_turns = 0   # 지금 Seed에서 k가 변하지 않은 턴이 연속 몇 번인지

    # ── 피드백 ───────────────────────────────────────────

    def observe(self, k_before: int, k_after: int) -> FuzzerFeedback:
        """
        input : 직전 턴 commit 전후의 k_run
        output : 한재이 파트 FuzzerFeedback

        risk_gain은 risk_defender와 같은 정의(1/k, k=0이면 0)로 계산해 넘긴다.
        """
        feedback = evaluate_feedback(
            k_before=k_before,
            k_after=k_after,
            risk_gain=_risk(k_after) - _risk(k_before),
            threshold=self._config.risk_threshold,
        )
        self._last_feedback = feedback
        self._stale_turns = self._stale_turns + 1 if feedback.status == "unchanged" else 0
        return feedback

    # ── 다음 질문 ────────────────────────────────────────

    def next_query(self, view: AttackStateView) -> AttackStep:
        """
        input : view (공개된 정보로 만든 State 요약)
        output : AttackStep

        지금 State에 맞는 Seed가 없으면 NoApplicableSeedError.
        """
        self._turn += 1
        feedback = self._last_feedback
        self._last_feedback = None   # 한 번 쓴 피드백을 다음 턴에 다시 쓰지 않음

        strategy = select_mutation_strategy(feedback) if feedback is not None else None
        reset_reason = self._reset_reason(view, feedback, strategy)

        if reset_reason is not None:
            seed, applicable = self._selector.choose(view)
            if seed is None:
                raise NoApplicableSeedError("지금 State에 맞는 Seed가 없습니다 (NO_APPLICABLE_ACTION).")
            bound = self._selector.bind(seed, view)
            instruction = None
            user_prompt = bound            # 한재이 main.py Turn 0: Seed를 그대로 Mutation LLM에
            strategy = None
            self._stale_turns = 0
        else:
            seed = self._current_seed
            bound = self._current_bound_text
            applicable = len(self._selector.applicable(view))
            instruction = generate_next_mutation(previous_prompt=self._current_query, strategy=strategy)
            user_prompt = instruction

        query, attempts, valid, reasons, usage = self._realize(user_prompt)
        fallback = not valid
        if fallback:
            query = bound   # 변형이 끝내 무효면 사람이 만든 Seed 문장을 그대로 보낸다 (항상 유효)

        self._current_seed, self._current_bound_text, self._current_query = seed, bound, query

        return AttackStep(
            turn=self._turn,
            query=query,
            seed_id=seed.seed_id,
            seed_version=seed.seed_version,
            objective=seed.objective,
            scenario_code=seed.scenario_code,
            target_qi=seed.target_qi,
            reset_reason=reset_reason,
            strategy=strategy,
            feedback_status=feedback.status if feedback is not None else None,
            bound_seed_text=bound,
            mutation_instruction=instruction,
            mutation_attempts=attempts,
            mutation_valid=valid,
            invalid_reasons=tuple(reasons),
            fallback_used=fallback,
            applicable_seed_count=applicable,
            mutation_input_tokens=usage[0],
            mutation_output_tokens=usage[1],
            mutation_latency_ms=usage[2],
        )

    def _reset_reason(self, view: AttackStateView, feedback: FuzzerFeedback | None,
                      strategy: str | None) -> str | None:
        if self._current_seed is None:
            return "FIRST_TURN"
        if feedback is None:
            return "NO_FEEDBACK"
        if strategy == ABORT_STRATEGY:
            return "ABORT"
        if not self._selector.is_applicable(self._current_seed, view):
            return "SEED_NOT_APPLICABLE"
        limit = self._config.max_stale_turns
        if limit is not None and self._stale_turns >= limit:
            return "STALE"
        return None

    # ── Mutation LLM 호출 + 유효성 검사 ─────────────────

    def _realize(self, user_prompt: str) -> tuple[str, int, bool, list[str], tuple[int, int, float]]:
        """
        Mutation LLM으로 질문 문장을 만든다. 무효하면 max_mutation_retries만큼 다시 요청한다.
        D1 Stage 8: 무효한 변형은 Target에 보내지 않는다 (호출 측이 fallback 처리).
        """
        reasons: list[str] = []
        text = ""
        in_tokens = out_tokens = 0
        latency = 0.0
        attempts = 0

        for attempts in range(1, self._config.max_mutation_retries + 2):
            response = self._client.complete(
                user_prompt,
                system_prompt=self._system_prompt,
                max_tokens=MUTATION_MAX_TOKENS,
            )
            in_tokens += response.input_tokens or 0
            out_tokens += response.output_tokens or 0
            latency += response.latency_ms

            text = response.text.strip()
            problem = self._invalid_reason(text, response)
            if problem is None:
                return text, attempts, True, reasons, (in_tokens, out_tokens, latency)
            reasons.append(problem)

        return text, attempts, False, reasons, (in_tokens, out_tokens, latency)

    def _invalid_reason(self, text: str, response: LLMResponse) -> str | None:
        """구조 검사만 한다. 의미 검사(같은 QI를 노리는지 등)는 D1 Pilot 전 Fuzzer 담당과 정할 항목이다."""
        if response.truncated:
            return "TRUNCATED"
        if not text:
            return "EMPTY"
        if len(text) > self._config.max_query_chars:
            return "TOO_LONG"
        if _UNRESOLVED_PLACEHOLDER.search(text):
            return "UNRESOLVED_PLACEHOLDER"
        return None


# ──────────────────────────────────────────────────────────────
# 내부 함수
# ──────────────────────────────────────────────────────────────

def _risk(k: int) -> float:
    """risk_defender/risk.py calculate_k의 risk와 같은 정의."""
    return 1.0 / k if k > 0 else 0.0


def _load_system_prompt(path: Path = FUZZER_SYSTEM_PROMPT_FILE) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Fuzzer system prompt 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Fuzzer system prompt 파일이 비어 있습니다: {path}")
    return text
