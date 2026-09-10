from pathlib import Path
from dotenv import load_dotenv
import os


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

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