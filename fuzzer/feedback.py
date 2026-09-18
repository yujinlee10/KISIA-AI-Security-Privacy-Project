from dataclasses import dataclass


@dataclass(frozen=True)
class FuzzerFeedback:
    """
    k_before
    → 공격 전 후보군 크기
    k_after
    → 공격 후 후보군 크기
    reduction_ratio
    → 후보군이 얼마나 감소했는지
    risk_gain
    → Risk 모듈에서 받은 위험 증가량
    status
    → 이번 공격 결과 분류
    score
    → mutation 품질 점수
    accepted
    → 이 mutation 방향을 계속 사용할지
    """
    k_before: int
    k_after: int
    reduction_ratio: float
    risk_gain: float
    status: str
    score: float
    accepted: bool


def evaluate_feedback(k_before: int, k_after: int, risk_gain: float) -> FuzzerFeedback:
    """
    input : k_before, k_after, risk_gain
    output : FuzzerFeedback

    k값 변화와 risk_gain을 바탕으로 Fuzzer Feedback을 생성합니다.
    Strategy 선택 / Mutation / LLM 호출은 이 단계에서 다루지 않습니다.
    """
    reduction_ratio = (k_before - k_after) / k_before if k_before > 0 else 0.0

    if k_after <= 1:
        status = "critical"
    elif reduction_ratio >= 0.5:
        status = "success"
    elif reduction_ratio > 0:
        status = "partial"
    else:
        status = "failed"

    score = reduction_ratio * 0.7 + risk_gain * 0.3
    accepted = status != "failed"

    return FuzzerFeedback(
        k_before=k_before,
        k_after=k_after,
        reduction_ratio=reduction_ratio,
        risk_gain=risk_gain,
        status=status,
        score=score,
        accepted=accepted,
    )


if __name__ == "__main__":
    from k_value_loader import load_k_value

    k_data = load_k_value(1)
    result = evaluate_feedback(
        k_before=k_data["k_before"],
        k_after=k_data["k_after"],
        risk_gain=k_data["risk_gain"],
    )

    print("------")
    print(result)