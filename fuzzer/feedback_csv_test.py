import csv

#경로 읽어오기
CSV_FILE = "risk_defender_demo.csv"


def load_k_values():
    """
    input : CSV 파일 경로
    output : k_before, k_after, risk_gain 값이 담긴 리스트
    csv 파일에서 k_before, k_after, risk_gain 값을 읽어오는 함수입니다.
    """
    k_values = []

    with open(CSV_FILE, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            data = {
                "turn": int(row["turn"]),
                "candidate_qi": row["candidate_qi"],
                "k_before": int(row["k_before"]),
                "k_after": int(row["k_after_raw"]),
                "risk_gain": float(row["risk_gain_raw"]),
            }

            k_values.append(data)

    return k_values


if __name__ == "__main__":
    results = load_k_values()

    for result in results:
        print("------")
        print("Turn:", result["turn"])
        print("Candidate QI:", result["candidate_qi"])
        print("k_before:", result["k_before"])
        print("k_after:", result["k_after"])
        print("risk_gain:", result["risk_gain"])