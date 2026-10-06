# Defender 판정대로 응답을 고쳐 쓰는 Rewrite LLM 호출
#
# 역할 구분 (D1 Stage 11)
#   무엇을 어느 수준으로 가릴지는 Backend(risk_defender)가 정한다.
#   Rewrite LLM은 그 결정을 문장으로 옮기기만 한다. 일반화 수준이나 대상을 스스로 고르지 않는다.
#   고쳐 쓴 결과가 실제로 안전한지는 release_gate가 다시 추출해 k를 계산해서 확인한다.
#
# Target·Extractor·Fuzzer와 같은 core LLM client(같은 .env, 같은 모델)를 쓴다.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, Sequence

from core.llm_client import LLMResponse

BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

DEFAULT_PROMPT_VERSION = "rewriter_system_v2"   # v2: 삭제 지시에서 인용문·다른 단서까지 빼도록 규칙 3을 구체화 (10/06 실행에서 v1이 인용문을 남김)

RESPONSE_SECTION_HEADER = "[원래 응답]"
INSTRUCTION_SECTION_HEADER = "[수정 지시]"

# risk_defender 일반화 노드 이름을 문장에 쓸 표현으로 옮긴 것 (한국어 / 영어).
# normalizer/rules.py가 이 표현을 다시 같은 노드로 읽을 수 있어야 한다 (재추출 후 commit).
# 국가 이름처럼 그대로 쓸 수 있는 값은 여기에 없다.
GENERALIZED_PHRASES: dict[str, tuple[str, str]] = {
    "adult": ("성인", "adult"),
    "senior": ("고령층", "senior"),
    "young": ("10대", "teenager"),
    "higher_education": ("대학 이상의 학력", "higher education"),
    "non_advanced_degree": ("학위 미취득", "no advanced degree"),
    "partnered": ("파트너가 있음", "partnered"),
    "not_partnered": ("파트너가 없음", "not partnered"),
}


class RewriteAction(str, Enum):
    GENERALIZE = "GENERALIZE"
    SUPPRESS = "SUPPRESS"


class RewriteError(RuntimeError):
    """고쳐 쓴 결과를 쓸 수 없을 때 (비어 있음, 잘림). 공개 게이트가 fallback으로 처리한다."""


class CompletionClient(Protocol):
    def complete(self, user_prompt: str, *, system_prompt: str | None = None,
                 max_tokens: int | None = None, temperature: float | None = None) -> LLMResponse:
        ...


@dataclass(frozen=True)
class RewriteInstruction:
    """
    observation_id / attribute
    → 어떤 관찰에 대한 지시인지
    action
    → GENERALIZE / SUPPRESS
    raw_value / evidence
    → 응답에 나온 표현과 근거 문구 (Rewrite LLM이 고칠 위치를 찾는 데 씀)
    replacement
    → 일반화할 때 바꿀 값 (Defender가 정한 값. State에 들어가는 형식). 삭제면 None
    replacement_text
    → 응답 문장에 쓸 표현. 없으면 replacement에서 만든다 (온톨로지 node_id처럼 문장에 못 쓰는 값일 때 채워진다)
    """
    observation_id: str
    attribute: str
    action: RewriteAction
    raw_value: str
    evidence: tuple[str, ...]
    replacement: str | None = None
    replacement_text: str | None = None

    @property
    def phrase(self) -> str:
        """지시문에 넣을 '바꿀 표현'."""
        return self.replacement_text or replacement_phrase(self.replacement or "")

    def to_log_dict(self) -> dict[str, Any]:
        return {"observation_id": self.observation_id, "attribute": self.attribute,
                "action": self.action.value, "raw_value": self.raw_value,
                "evidence": list(self.evidence), "replacement": self.replacement,
                "replacement_text": self.phrase if self.action is RewriteAction.GENERALIZE else None}


@dataclass(frozen=True)
class RewriterConfig:
    """
    prompt_version
    → prompts/ 안의 system prompt 파일 이름
    max_tokens
    → None이면 core 설정값 (Target과 같은 상한이라 원래 응답 길이를 담을 수 있다)
    temperature
    → 0.0. 같은 지시에 같은 결과가 나오도록 고정
    """
    prompt_version: str = DEFAULT_PROMPT_VERSION
    max_tokens: int | None = None
    temperature: float | None = 0.0


@dataclass(frozen=True)
class RewriteResult:
    text: str
    instructions: tuple[RewriteInstruction, ...]
    prompt_version: str
    response_model: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "instructions": [item.to_log_dict() for item in self.instructions],
            "prompt_version": self.prompt_version,
            "response_model": self.response_model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
        }


class ResponseRewriter:
    def __init__(self, client: CompletionClient, config: RewriterConfig = RewriterConfig()) -> None:
        self._client = client
        self._config = config
        self._system_prompt = load_prompt(config.prompt_version)

    def rewrite(self, response: str, instructions: Sequence[RewriteInstruction]) -> RewriteResult:
        """
        input : response (고칠 응답 원문), instructions (수정 지시. 비어 있으면 ValueError)
        output : RewriteResult

        LLM 호출 오류(core.llm_client.LLMError)는 그대로 올린다.
        결과가 비었거나 max_tokens로 잘렸으면 RewriteError.
        """
        if not instructions:
            raise ValueError("수정 지시가 비어 있습니다.")

        llm = self._client.complete(
            build_user_prompt(response, instructions),
            system_prompt=self._system_prompt,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )
        text = llm.text.strip()
        if llm.truncated:
            raise RewriteError("고쳐 쓴 응답이 max_tokens로 잘렸습니다.")
        if not text:
            raise RewriteError("고쳐 쓴 응답이 비어 있습니다.")

        return RewriteResult(
            text=text,
            instructions=tuple(instructions),
            prompt_version=self._config.prompt_version,
            response_model=llm.model,
            input_tokens=llm.input_tokens or 0,
            output_tokens=llm.output_tokens or 0,
            latency_ms=llm.latency_ms,
        )


# ──────────────────────────────────────────────────────────────
# prompt 조립 (순수 함수)
# ──────────────────────────────────────────────────────────────

def load_prompt(prompt_version: str) -> str:
    path = PROMPT_DIR / f"{prompt_version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"system prompt 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"system prompt 파일이 비어 있습니다: {path}")
    return text


def replacement_phrase(value: str) -> str:
    """일반화 값을 지시문에 쓸 표현으로 바꾼다. 노드 이름이면 '한국어 (영어)', 아니면 값 그대로."""
    phrase = GENERALIZED_PHRASES.get(value)
    return f"{phrase[0]} ({phrase[1]})" if phrase else value


def build_user_prompt(response: str, instructions: Sequence[RewriteInstruction]) -> str:
    lines = []
    for number, item in enumerate(instructions, start=1):
        evidence = " / ".join(f'"{text}"' for text in item.evidence) or "(없음)"
        line = (f"{number}. {'일반화' if item.action is RewriteAction.GENERALIZE else '삭제'}"
                f" | 속성: {item.attribute} | 응답에 나온 값: \"{item.raw_value}\" | 근거 문구: {evidence}")
        if item.action is RewriteAction.GENERALIZE:
            line += f" | 바꿀 표현: {item.phrase}"
        lines.append(line)
    return (f"{RESPONSE_SECTION_HEADER}\n{response.strip()}\n\n"
            f"{INSTRUCTION_SECTION_HEADER}\n" + "\n".join(lines))
