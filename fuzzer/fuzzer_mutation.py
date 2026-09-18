from pathlib import Path
import os

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent
ATTACK_SEED_FILE = os.path.join(BASE_DIR, "seedinput",  "attackseed.txt")

def attackseed_generator():
    """
    input : attackseed.txt
    output : 1차 mutation축을 변경한 공격 프롬프트 리스트


    attackseed.txt 파일을 읽어 공격 프롬프트를 생성합니다.
    현재는 테스트용이기에 간단하게 작성하여
    문장의 {target} 부분을 targets리스트 안의 값들로 변경하여 attackseed 리스트를 생성합니다.
    """

    targets = [
        "AGE",
        "LOC",
        "OCCP",
        "EDU",
        "SEX",
        "INC"
    ]

    # attackseed.txt 읽기
    with open(ATTACK_SEED_FILE, "r", encoding="utf-8") as f:
        seed_prompt = f.read().strip()

    attack_prompts = []

    for target in targets:
        attack_prompt = seed_prompt.replace("{target}", target)
        attack_prompts.append(attack_prompt)
    
    print("공격 프롬프트 1차 mutation 생성 완료.")

    return attack_prompts