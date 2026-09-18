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


def evaluate_feedback(
    k_before: int, 
    k_after: int, 
    risk_gain: float,
    threshold: int = 5
    ) -> FuzzerFeedback:
    """
    input : k_before, k_after, risk_gain
    output : FuzzerFeedback

    k값 변화와 risk_gain을 바탕으로 Fuzzer Feedback을 생성합니다.
    """
    # 잘못된 입력
    if k_before <= 0 or k_after < 0:
        return FuzzerFeedback(
            k_before=k_before,
            k_after=k_after,
            reduction_ratio=0.0,
            risk_gain=risk_gain,
            status="invalid",
            score=-1.0,
            accepted=False,
        )

    reduction_ratio = (k_before - k_after) / k_before

    # 후보가 0명인 경우
    if k_after == 0:
        status = "invalid"
        score = -1.0
        accepted = False

    # 위험 threshold 도달
    elif k_after <= threshold:
        status = "threshold_reached"
        score = reduction_ratio
        accepted = True

    # 후보군 감소
    elif k_after < k_before:
        status = "improved"
        score = reduction_ratio
        accepted = True

    # 변화 없음
    elif k_after == k_before:
        status = "unchanged"
        score = 0.0
        accepted = False

    # 후보군 증가
    else:
        status = "worse"
        score = reduction_ratio
        accepted = False

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