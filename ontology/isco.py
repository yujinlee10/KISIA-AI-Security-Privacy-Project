# 직업 분류: ISCO-08 (ILO 국제표준직업분류) 4단계와, 직업 표기 → ISCO 코드 대응표
#
# 코드 자릿수가 곧 단계다.  1자리 Major / 2자리 Sub-major / 3자리 Minor / 4자리 Unit
#   "2512" (Software developers) → "251" → "25" → "2" (Professionals) → ANY
# 부모는 코드의 마지막 자리를 뗀 것이다. 자체 직업 분류를 만들지 않는다 (D1).
#
# 여기에는 ISCO-08 전체(436개 Unit)가 아니라, 대응표가 가리키는 코드와 그 조상만 담았다.
# 대응표에 새 코드를 쓰려면 이 파일의 ISCO_LABELS에 그 코드와 조상의 이름을 먼저 넣어야 한다 (없으면 적재 오류).
#
# 대응표: ontology/data/occp_alias_v0.1.csv
#   alias      : 소문자 직업 표기 (retired·part-time·freelance·junior·senior 같은 수식어를 뗀 것)
#   isco_code  : 그 표기가 뒷받침하는 가장 구체적인 ISCO 코드. 구분이 안 되면 상위 코드에서 멈춘다.
#                "X"는 ISCO로 분류할 수 없는 값(은퇴 상태만 있는 경우 등)을 모으는 보조 node다.
#   confidence : H(분명) / M(통상적 해석) / L(추정) — M·L 행은 사람 검토 대상
#   note       : 판단 근거
# 이 표는 SynthPAI 300명의 직업 표기 221종을 기계로 초안 작성한 것이다. 연구 설계 담당의 검토 전이다.

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_ALIAS_FILE = DATA_DIR / "occp_alias_v0.1.csv"

UNCLASSIFIED_CODE = "X"
UNCLASSIFIED_LABELS = ("직업 분류 불가", "occupation not classifiable")

LEVEL_BY_LENGTH = {1: "major", 2: "sub_major", 3: "minor", 4: "unit"}

# 직업 표기 앞에 붙어 고용 형태·경력만 나타내는 말. 직업 자체를 바꾸지 않으므로 떼고 대조한다.
_MODIFIER = re.compile(r"^(?:retired|part[- ]time|full[- ]time|freelance|junior|senior|former)\s+")

# code → (한국어 이름, 영어 이름). 영어는 ISCO-08 공식 그룹 이름, 한국어는 그 뜻을 옮긴 것이다.
# Unit(4자리)은 일반화의 목적지가 되지 않으므로 한국어 이름을 따로 두지 않았다 (영어 이름을 쓴다).
ISCO_LABELS: dict[str, tuple[str | None, str]] = {
    # ── Major ──
    "1": ("관리자", "Managers"),
    "2": ("전문가", "Professionals"),
    "3": ("기술공 및 준전문가", "Technicians and associate professionals"),
    "4": ("사무 종사자", "Clerical support workers"),
    "5": ("서비스·판매 종사자", "Service and sales workers"),
    "6": ("농림어업 숙련 종사자", "Skilled agricultural, forestry and fishery workers"),
    "7": ("기능원 및 관련 기능 종사자", "Craft and related trades workers"),
    "8": ("장치·기계 조작 및 조립 종사자", "Plant and machine operators, and assemblers"),
    "9": ("단순노무 종사자", "Elementary occupations"),
    # ── Sub-major ──
    "11": ("최고경영자·고위 공무원", "Chief executives, senior officials and legislators"),
    "12": ("행정·경영 관리자", "Administrative and commercial managers"),
    "13": ("생산·전문 서비스 관리자", "Production and specialized services managers"),
    "14": ("숙박·소매·기타 서비스 관리자", "Hospitality, retail and other services managers"),
    "21": ("과학·공학 전문가", "Science and engineering professionals"),
    "22": ("보건 전문가", "Health professionals"),
    "23": ("교육 전문가", "Teaching professionals"),
    "24": ("경영·행정 전문가", "Business and administration professionals"),
    "25": ("정보통신기술 전문가", "Information and communications technology professionals"),
    "26": ("법률·사회·문화 전문가", "Legal, social and cultural professionals"),
    "32": ("보건 준전문가", "Health associate professionals"),
    "33": ("경영·행정 준전문가", "Business and administration associate professionals"),
    "34": ("법률·사회·문화 준전문가", "Legal, social, cultural and related associate professionals"),
    "35": ("정보통신 기술공", "Information and communications technicians"),
    "41": ("일반 사무원", "General and keyboard clerks"),
    "42": ("고객 서비스 사무원", "Customer services clerks"),
    "44": ("기타 사무원", "Other clerical support workers"),
    "51": ("대인 서비스 종사자", "Personal service workers"),
    "52": ("판매 종사자", "Sales workers"),
    "53": ("돌봄 종사자", "Personal care workers"),
    "54": ("보안·경호 서비스 종사자", "Protective services workers"),
    "61": ("농업 숙련 종사자", "Market-oriented skilled agricultural workers"),
    "62": ("임업·어업 숙련 종사자", "Market-oriented skilled forestry, fishery and hunting workers"),
    "71": ("건설 기능 종사자", "Building and related trades workers, excluding electricians"),
    "83": ("운전·운송 종사자", "Drivers and mobile plant operators"),
    "93": ("광업·건설·제조·운송 단순노무자", "Labourers in mining, construction, manufacturing and transport"),
    # ── Minor ──
    "111": ("입법자·고위 공무원", "Legislators and senior officials"),
    "112": ("최고경영자", "Managing directors and chief executives"),
    "121": ("경영지원·행정 관리자", "Business services and administration managers"),
    "122": ("영업·마케팅·개발 관리자", "Sales, marketing and development managers"),
    "131": ("농림어업 생산 관리자", "Production managers in agriculture, forestry and fisheries"),
    "134": ("전문 서비스 관리자", "Professional services managers"),
    "141": ("호텔·음식점 관리자", "Hotel and restaurant managers"),
    "143": ("기타 서비스 관리자", "Other services managers"),
    "211": ("물리·지구과학 전문가", "Physical and earth science professionals"),
    "213": ("생명과학 전문가", "Life science professionals"),
    "214": ("공학 전문가 (전기 분야 제외)", "Engineering professionals (excluding electrotechnology)"),
    "215": ("전기·전자 공학 기술자", "Electrotechnology engineers"),
    "216": ("건축가·도시계획가·디자이너", "Architects, planners, surveyors and designers"),
    "221": ("의사", "Medical doctors"),
    "222": ("간호·조산 전문가", "Nursing and midwifery professionals"),
    "226": ("기타 보건 전문가", "Other health professionals"),
    "231": ("대학 교원", "University and higher education teachers"),
    "233": ("중등 교원", "Secondary education teachers"),
    "235": ("기타 교육 전문가", "Other teaching professionals"),
    "241": ("재무 전문가", "Finance professionals"),
    "242": ("행정 전문가", "Administration professionals"),
    "243": ("영업·마케팅·홍보 전문가", "Sales, marketing and public relations professionals"),
    "251": ("소프트웨어·응용프로그램 개발자 및 분석가", "Software and applications developers and analysts"),
    "261": ("법률 전문가", "Legal professionals"),
    "262": ("사서·기록물관리사·큐레이터", "Librarians, archivists and curators"),
    "263": ("사회·종교 전문가", "Social and religious professionals"),
    "264": ("작가·언론인·언어 전문가", "Authors, journalists and linguists"),
    "265": ("창작·공연 예술가", "Creative and performing artists"),
    "325": ("기타 보건 준전문가", "Other health associate professionals"),
    "333": ("사업 서비스 중개인", "Business services agents"),
    "334": ("행정·전문 비서", "Administrative and specialized secretaries"),
    "342": ("스포츠·피트니스 종사자", "Sports and fitness workers"),
    "343": ("예술·문화·조리 준전문가", "Artistic, cultural and culinary associate professionals"),
    "351": ("정보통신 운영·사용자 지원 기술공", "Information and communications technology operations and user support technicians"),
    "411": ("일반 사무원 (세분류)", "General office clerks"),
    "422": ("고객 안내 사무원", "Client information workers"),
    "441": ("기타 사무 지원 종사자", "Other clerical support workers"),
    "511": ("여행 안내·승무 종사자", "Travel attendants, conductors and guides"),
    "512": ("조리사", "Cooks"),
    "513": ("웨이터·바텐더", "Waiters and bartenders"),
    "514": ("미용 관련 종사자", "Hairdressers, beauticians and related workers"),
    "522": ("매장 판매원", "Shop salespersons"),
    "532": ("보건 분야 돌봄 종사자", "Personal care workers in health services"),
    "541": ("보안·경호 서비스 종사자 (세분류)", "Protective services workers"),
    "622": ("어업 종사자·수렵인", "Fishery workers, hunters and trappers"),
    "711": ("건축 골조 관련 기능 종사자", "Building frame and related trades workers"),
    "832": ("승용차·승합차·오토바이 운전원", "Car, van and motorcycle drivers"),
    "833": ("대형 화물차·버스 운전원", "Heavy truck and bus drivers"),
    "931": ("광업·건설 단순노무자", "Mining and construction labourers"),
    # ── Unit ──
    "1112": (None, "Senior government officials"),
    "1120": (None, "Managing directors and chief executives"),
    "1211": (None, "Finance managers"),
    "1212": (None, "Human resource managers"),
    "1221": (None, "Sales and marketing managers"),
    "1222": (None, "Advertising and public relations managers"),
    "1311": (None, "Agricultural and forestry production managers"),
    "1345": (None, "Education managers"),
    "1346": (None, "Financial and insurance services branch managers"),
    "1411": (None, "Hotel managers"),
    "1431": (None, "Sports, recreation and cultural centre managers"),
    "2111": (None, "Physicists and astronomers"),
    "2113": (None, "Chemists"),
    "2114": (None, "Geologists and geophysicists"),
    "2131": (None, "Biologists, botanists, zoologists and related professionals"),
    "2133": (None, "Environmental protection professionals"),
    "2142": (None, "Civil engineers"),
    "2143": (None, "Environmental engineers"),
    "2144": (None, "Mechanical engineers"),
    "2146": (None, "Mining engineers, metallurgists and related professionals"),
    "2151": (None, "Electrical engineers"),
    "2161": (None, "Building architects"),
    "2163": (None, "Product and garment designers"),
    "2164": (None, "Town and traffic planners"),
    "2166": (None, "Graphic and multimedia designers"),
    "2212": (None, "Specialist medical practitioners"),
    "2221": (None, "Nursing professionals"),
    "2263": (None, "Environmental and occupational health and hygiene professionals"),
    "2264": (None, "Physiotherapists"),
    "2265": (None, "Dieticians and nutritionists"),
    "2269": (None, "Health professionals not elsewhere classified"),
    "2310": (None, "University and higher education teachers"),
    "2330": (None, "Secondary education teachers"),
    "2353": (None, "Other language teachers"),
    "2411": (None, "Accountants"),
    "2412": (None, "Financial and investment advisers"),
    "2413": (None, "Financial analysts"),
    "2421": (None, "Management and organization analysts"),
    "2423": (None, "Personnel and careers professionals"),
    "2431": (None, "Advertising and marketing professionals"),
    "2432": (None, "Public relations professionals"),
    "2512": (None, "Software developers"),
    "2513": (None, "Web and multimedia developers"),
    "2611": (None, "Lawyers"),
    "2621": (None, "Archivists and curators"),
    "2622": (None, "Librarians and related information professionals"),
    "2631": (None, "Economists"),
    "2632": (None, "Sociologists, anthropologists and related professionals"),
    "2633": (None, "Philosophers, historians and political scientists"),
    "2634": (None, "Psychologists"),
    "2635": (None, "Social work and counselling professionals"),
    "2641": (None, "Authors and related writers"),
    "2642": (None, "Journalists"),
    "2643": (None, "Translators, interpreters and other linguists"),
    "2651": (None, "Visual artists"),
    "2654": (None, "Film, stage and related directors and producers"),
    "3257": (None, "Environmental and occupational health inspectors and associates"),
    "3332": (None, "Conference and event planners"),
    "3343": (None, "Administrative and executive secretaries"),
    "3423": (None, "Fitness and recreation instructors and program leaders"),
    "3432": (None, "Interior designers and decorators"),
    "3434": (None, "Chefs"),
    "4110": (None, "General office clerks"),
    "4226": (None, "Receptionists (general)"),
    "4411": (None, "Library clerks"),
    "4412": (None, "Mail carriers and sorting clerks"),
    "5113": (None, "Travel guides"),
    "5120": (None, "Cooks"),
    "5131": (None, "Waiters"),
    "5132": (None, "Bartenders"),
    "5141": (None, "Hairdressers"),
    "5221": (None, "Shopkeepers"),
    "5223": (None, "Shop sales assistants"),
    "5412": (None, "Police officers"),
    "7115": (None, "Carpenters and joiners"),
    "8322": (None, "Car, taxi and van drivers"),
    "8331": (None, "Bus and tram drivers"),
    "9313": (None, "Building construction labourers"),
}


class OccupationTableError(ValueError):
    """대응표가 잘못됐을 때 (모르는 코드, 중복 표기 등)."""


@dataclass(frozen=True)
class OccupationAlias:
    alias: str
    isco_code: str
    confidence: str
    note: str


@dataclass(frozen=True)
class OccupationTable:
    """
    aliases : 표기 → OccupationAlias
    codes   : 대응표가 쓰는 코드와 그 조상 전부 (온톨로지에 node로 만들어진다)
    sha256  : 대응표 파일 해시 (실행 기록용)
    """
    aliases: dict[str, OccupationAlias]
    codes: frozenset[str]
    path: str
    sha256: str

    def lookup(self, text: str) -> OccupationAlias | None:
        return self.aliases.get(clean_occupation(text))


def clean_occupation(text: str) -> str:
    """
    input : 직업 표기 ("Retired University Lecturer", "a part-time barista.")
    output : 대조용 표기 ("university lecturer", "barista")

    소문자·공백 정리, 앞의 관사와 끝 마침표 제거, 고용 형태·경력 수식어 제거만 한다.
    """
    cleaned = " ".join(text.strip().lower().split()).rstrip(".")
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _MODIFIER.sub("", cleaned)
    return cleaned


def code_ancestors(code: str) -> tuple[str, ...]:
    """'2512' → ('251', '25', '2'). 'X'는 조상이 없다."""
    if code == UNCLASSIFIED_CODE:
        return ()
    return tuple(code[:length] for length in range(len(code) - 1, 0, -1))


def load_occupation_table(path: str | Path = DEFAULT_ALIAS_FILE) -> OccupationTable:
    """
    input : 대응표 CSV 경로
    output : OccupationTable

    코드가 숫자 1~4자리 또는 'X'가 아니거나, 그 코드·조상의 이름이 ISCO_LABELS에 없으면 오류.
    """
    path = Path(path)
    raw = path.read_bytes()
    aliases: dict[str, OccupationAlias] = {}
    codes: set[str] = set()

    for row in csv.DictReader(raw.decode("utf-8").splitlines()):
        alias = clean_occupation(row["alias"])
        code = row["isco_code"].strip()
        if not alias:
            raise OccupationTableError("alias가 빈 행이 있습니다.")
        if alias in aliases:
            raise OccupationTableError(f"표기 중복: {alias}")
        if code != UNCLASSIFIED_CODE:
            if not (code.isdigit() and 1 <= len(code) <= 4):
                raise OccupationTableError(f"{alias}: 잘못된 ISCO 코드 {code!r}")
            missing = [c for c in (code, *code_ancestors(code)) if c not in ISCO_LABELS]
            if missing:
                raise OccupationTableError(f"{alias}: ISCO_LABELS에 이름이 없는 코드 {missing}")
            codes.update((code, *code_ancestors(code)))
        confidence = row.get("confidence", "").strip().upper()
        if confidence not in {"H", "M", "L"}:
            raise OccupationTableError(f"{alias}: confidence는 H/M/L 중 하나여야 합니다.")
        aliases[alias] = OccupationAlias(alias, code, confidence, (row.get("note") or "").strip())

    return OccupationTable(aliases, frozenset(codes), path.name, hashlib.sha256(raw).hexdigest())
