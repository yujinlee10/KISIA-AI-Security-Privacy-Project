# 검색된 persona 글을 문맥으로 LLM을 호출해 Target의 raw_candidate_response를 만드는 파일
#
# 이 파일이 만드는 것은 "공개 전 초안"이다 (D1 Stage 11, HANDOFF §4.1 3번).
#   - 공격자(Fuzzer)에게 바로 넘기지 않는다.
#   - Privacy State에 바로 commit하지 않는다.
#   - 공개 여부는 orchestrator의 공개 게이트가 정한다. C0·C2에서는 그대로 통과, C1·C3에서는 Defender 검사.
#   그래서 반환 객체에 released_response 필드를 두지 않는다. Target이 공개를 결정하는 것처럼 보이지 않게 하기 위함이다.
#
# 생성 전 규칙 주입 지점 (D1 Stage 12, C2·C3 조건)
#   respond(rule_constraints=...)로 받은 value-free 규칙을 system prompt 끝에 붙인다.
#   POC에서는 비어 있고(C0·C1), 경량 언러닝 파트에서 Rule Retriever가 채운다.
#   규칙 내용 검증(비밀값 미포함)은 Rule Store 책임이며 여기서는 그대로 붙이기만 한다.
#
# system prompt에 개인정보 보호 지시를 넣지 않는 이유
#   보호 행동은 C2(규칙 주입)·C1(Defender)로만 켜야 조건 비교가 성립한다.
#   기본 prompt에 보호 지시가 있으면 C0 기준선이 이미 방어된 상태가 된다.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from core.llm_client import LLMResponse
from target_llm.retriever import RetrievalResult, Retriever


BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

# prompt 파일 이름이 곧 버전이다. 문구를 바꾸면 새 파일(v2)을 만들고 기존 파일은 고치지 않는다.
DEFAULT_PROMPT_VERSION = "target_system_v1"

RULE_SECTION_HEADER = "[응답 생성 규칙]"
CONTEXT_SECTION_HEADER = "[검색된 사용자 기록]"
QUESTION_SECTION_HEADER = "[질문]"


class EmptyContextError(RuntimeError):
    """
    검색된 글이 하나도 없을 때. 문맥 없이 호출하면 LLM이 근거 없는 속성을 지어내고,
    그 값이 QI로 추출되어 State를 오염시킨다. 호출 전에 막는다.
    """


class CompletionClient(Protocol):
    """responder가 기대하는 LLM 클라이언트 모양. core.llm_client.LLMClient가 이를 만족한다."""

    def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        ...


@dataclass(frozen=True)
class TargetConfig:
    """
    prompt_version
    → 사용할 system prompt 파일 이름 (확장자 제외)
    max_tokens
    → None이면 core 설정값(기본 1200)
    temperature
    → None이면 보내지 않음(서버 기본값). Pilot 후 freeze할 연구 설정이다.
    """
    prompt_version: str = DEFAULT_PROMPT_VERSION
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class TargetOutput:
    """
    target_id / query
    → 이번 턴의 대상과 실제로 받은 질문 원문
    raw_candidate_response
    → 공개 전 초안 응답. 공격자·State에 직접 전달 금지
    retrieval
    → 검색 결과 (문맥에 들어간 글과 검색 설정)
    rules_injected
    → 생성 전에 주입된 규칙 수 (C0·C1은 0)
    prompt_version
    → 사용한 system prompt 버전
    requested_model / response_model
    → 요청한 모델과 실제 응답한 모델 (프록시 라우팅 확인용)
    stop_reason / response_truncated
    → 'max_tokens'로 잘렸는지. 잘린 응답은 Extractor가 따로 처리해야 한다
    input_tokens / output_tokens / latency_ms / attempts
    → 비용·성능 기록 (D1 Stage 15 Budget·비용)
    temperature / max_tokens
    → 실제 호출에 쓴 생성 설정 (None이면 서버·core 기본값)
    """
    target_id: str
    query: str
    raw_candidate_response: str
    retrieval: RetrievalResult
    rules_injected: int
    prompt_version: str
    requested_model: str | None
    response_model: str | None
    stop_reason: str | None
    response_truncated: bool
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    attempts: int
    temperature: float | None
    max_tokens: int | None

    def to_log_dict(self) -> dict[str, Any]:
        """
        input : 없음
        output : trusted 로그용 dict

        검색된 글 본문은 빼고 id만 남긴다. raw_candidate_response는 trusted 로그 전용이므로 포함한다.
        이 dict를 Fuzzer나 Mutation LLM prompt로 넘기지 않는다 (HANDOFF §14).
        """
        return {
            "target_id": self.target_id,
            "query": self.query,
            "raw_candidate_response": self.raw_candidate_response,
            "retrieved_comment_ids": list(self.retrieval.comment_ids),
            "retrieval_candidate_count": self.retrieval.candidate_count,
            "retrieval_context_chars": self.retrieval.context_chars,
            "retrieval_context_truncated": self.retrieval.context_truncated,
            "retriever_name": self.retrieval.retriever_name,
            "retriever_version": self.retrieval.retriever_version,
            "rules_injected": self.rules_injected,
            "prompt_version": self.prompt_version,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "stop_reason": self.stop_reason,
            "response_truncated": self.response_truncated,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


class TargetResponder:
    """
    Target System 한 개. retriever와 LLM client를 주입받아 조립한다.

    system prompt 파일은 생성 시 한 번만 읽는다 (fuzzer처럼 매 호출마다 파일을 열지 않음).
    """

    def __init__(
        self,
        retriever: Retriever,
        client: CompletionClient,
        config: TargetConfig = TargetConfig(),
        *,
        requested_model: str | None = None,
    ) -> None:
        """
        input : retriever, client, config, requested_model (로그용 요청 모델 ID. 보통 client.model)
        output : 없음
        """
        self._retriever = retriever
        self._client = client
        self._config = config
        self._requested_model = requested_model or getattr(client, "model", None)
        self._system_template = load_prompt(config.prompt_version)

    def respond(
        self,
        target_id: str,
        query: str,
        *,
        rule_constraints: Sequence[str] = (),
    ) -> TargetOutput:
        """
        input : target_id (이번 대화의 대상 persona), query (Fuzzer가 보낸 질문 원문),
                rule_constraints (생성 전 주입할 value-free 규칙. POC에서는 비움)
        output : TargetOutput (raw_candidate_response 포함)

        LLM 호출 오류(core.llm_client.LLMError 계열)는 잡지 않고 그대로 올린다.
        실패한 턴을 '응답 없음'으로 기록하면 QI 0개인 정상 턴과 구분이 안 되기 때문이다.
        """
        if not query or not query.strip():
            raise ValueError("query는 비어 있을 수 없습니다.")

        retrieval = self._retriever.retrieve(target_id, query)
        if not retrieval.comments:
            raise EmptyContextError(
                f"{target_id}에 대해 문맥에 넣을 글이 없습니다 "
                f"(범위 내 {retrieval.candidate_count}개, 상한 때문에 0개 선택)."
            )

        rules = [rule.strip() for rule in rule_constraints if rule and rule.strip()]
        system_prompt = build_system_prompt(self._system_template, rules)
        user_prompt = build_user_prompt(retrieval, query)

        response = self._client.complete(
            user_prompt,
            system_prompt=system_prompt,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )

        return TargetOutput(
            target_id=target_id,
            query=query,
            raw_candidate_response=response.text,
            retrieval=retrieval,
            rules_injected=len(rules),
            prompt_version=self._config.prompt_version,
            requested_model=self._requested_model,
            response_model=response.model,
            stop_reason=response.stop_reason,
            response_truncated=response.truncated,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
            attempts=response.attempts,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )


# ──────────────────────────────────────────────────────────────
# prompt 조립 (순수 함수 — 테스트·재현 확인이 쉽도록 클래스 밖에 둔다)
# ──────────────────────────────────────────────────────────────

def load_prompt(prompt_version: str) -> str:
    """
    input : prompt_version (prompts/ 안의 파일 이름, 확장자 제외)
    output : prompt 문자열
    """
    path = PROMPT_DIR / f"{prompt_version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"system prompt 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"system prompt 파일이 비어 있습니다: {path}")
    return text


def build_system_prompt(template: str, rules: Sequence[str]) -> str:
    """
    input : template (기본 system prompt), rules (주입할 규칙 목록)
    output : 최종 system prompt

    규칙이 없으면 template을 그대로 쓴다. C0와 C1의 system prompt가 글자 하나까지 같아야
    두 조건의 차이가 Defender 하나로만 설명된다.
    """
    if not rules:
        return template
    rule_lines = "\n".join(f"- {rule}" for rule in rules)
    return f"{template}\n\n{RULE_SECTION_HEADER}\n{rule_lines}"


def build_user_prompt(retrieval: RetrievalResult, query: str) -> str:
    """
    input : retrieval (문맥에 넣을 글), query (질문 원문)
    output : user 메시지

    글 앞에는 [1], [2] 같은 지역 번호만 붙인다. 원본 comment id는 LLM에 보여주지 않는다
    (응답에 id가 따라 나오면 Extractor가 의미 없는 토큰을 근거로 착각할 수 있음).
    """
    context_lines = "\n".join(
        f"[{index}] {comment.text.strip()}"
        for index, comment in enumerate(retrieval.comments, start=1)
    )
    return (
        f"{CONTEXT_SECTION_HEADER}\n{context_lines}\n\n"
        f"{QUESTION_SECTION_HEADER}\n{query.strip()}"
    )
