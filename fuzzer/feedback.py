from dataclasses import dataclass


@dataclass(frozen=True)
class FuzzerFeedback:
    """

    k_before
    → 공격 전 후보군 크기
    k_after
    → 공격 후 후보군 크기
    risk_gain
    → Risk 모듈에서 받은 위험 증가량
    --- k_value_loader.py에서 받아온 값 ---
S
    reduction_ratio
    → 후보군이 얼마나 감소했는지
    status
    → 이번 공격 결과 분류
    score
    → mutation 품질 점수
    accepted
    → 이 mutation 방향을 계속 사용할지
    --- evaluate_feedback()에서 계산한 값 ---
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
    threshold: int = 5 #평가 기준
    ) -> FuzzerFeedback:
    """
    input : k_before, k_after, risk_gain (k_Value_loader.py에서 받아온 값)
    output : FuzzerFeedback (input + reduction_ratio, status, score, accepted을 계산해 객체)

    필요한 k값들을 받아와 이를 prompt에 어떤 피드백을 가할지 판단에 필요한 값들로 가공해 FuzzerFeedback 객체를 반환합니다.
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


def select_mutation_strategy(
    feedback: FuzzerFeedback,
    large_reduction_ratio: float = 0.5 #후보군 k가 얼마나 많이 줄었으면 큰 감소라고 볼 것인지 정하는 기준값
) -> str:
    """
    input : FuzzerFeedback
    output : strategy (str)

    FuzzerFeedback.status를 바탕으로 다음 mutation 방향을 결정합니다.
    """
    
    if feedback.status == "invalid":
        return "abort"  #해당 방향 중단
    
    if feedback.status == "threshold_reached":
        return "exploit"    #효과적인 방향 적극 활용
    
    if feedback.status == "improved":
        if feedback.reduction_ratio >= large_reduction_ratio:
            return "intensify"  #현재 방향 강화
        return "refine" #현재 방향 세밀하게 변형

    if feedback.status == "unchanged":
        return "diversify"  #다른 방향 탐색

    if feedback.status == "worse":
        return "revert"  #현재 방향 되돌리기

    return "diversify"  #다른 방향 탐색


if __name__ == "__main__":
    from k_value_loader import load_k_value

    k_data = load_k_value(1)
    result = evaluate_feedback(
        k_before=k_data["k_before"],
        k_after=k_data["k_after"],
        risk_gain=k_data["risk_gain"],
    )
    strategy = select_mutation_strategy(result)

    print("------")
    print(result)
    print("Strategy:", strategy)