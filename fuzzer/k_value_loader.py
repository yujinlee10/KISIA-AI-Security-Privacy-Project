import csv

CSV_FILE = "risk_defender_demo.csv"


def load_k_value(turn):
    """
    input : CSV 파일 경로
    output : k_before, k_after, risk_gain 값이 담긴 리스트
    csv 파일에서 k_before, k_after, risk_gain 값을 읽어오는 함수입니다.
    """
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if int(row["turn"]) == turn:
                return {
                    "turn": int(row["turn"]),
                    "candidate_qi": row["candidate_qi"],
                    "k_before": int(row["k_before"]),
                    "k_after": int(row["k_after_raw"]),
                    "risk_gain": float(row["risk_gain_raw"]),
                }

    return None


if __name__ == "__main__":
    result = load_k_value(1)

    print("------")
    print("Turn:", result["turn"])
    print("Candidate QI:", result["candidate_qi"])
    print("k_before:", result["k_before"])
    print("k_after:", result["k_after"])
    print("risk_gain:", result["risk_gain"])