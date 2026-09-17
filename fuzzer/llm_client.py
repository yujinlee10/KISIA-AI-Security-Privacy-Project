from pathlib import Path
from dotenv import load_dotenv
import os
import json
import requests

BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"
SEED_PROMPT_FILE = BASE_DIR / "seedprompt.txt"

RESULT_DIR = BASE_DIR / "results"
RESULT_FILE = RESULT_DIR / "mutation_results.jsonl"


load_dotenv(ENV_FILE)


API_KEY = os.getenv("LLM_API_KEY")
API_BASE = os.getenv("LLM_API_BASE")
MODEL = os.getenv("LLM_MODEL")


print("API BASE:", API_BASE)
print("MODEL:", MODEL)

if API_KEY:
    print("API KEY: 정상적으로 불러왔습니다.")
else:
    print("API KEY: 불러오지 못했습니다.")

print("환경 변수 로드 완료.")
print("------")



def llm_request(attack_prompt):
    """
    input : attack_prompt
    output : LLM의 응답 결과

    LLM API에 공격 프롬프트를 전송하고 응답을 받아옵니다.
    claude-code api 규격을 기준으로 작성 되었습니다.
    """
    with open(SEED_PROMPT_FILE, "r", encoding="utf-8") as f:
        seed_prompt = f.read().strip()

    url = f"{API_BASE.rstrip('/')}/v1/messages"

    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "max_tokens": 512,

        "system": seed_prompt,

        "messages": [
            {
                "role": "user",
                "content": attack_prompt
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    print("STATUS:", response.status_code)
    print("RAW RESPONSE:", response.text)

    response.raise_for_status()

    result = response.json()

    return result["content"][0]["text"]
