# 고정 표: 국가, 그리고 값이 몇 개뿐인 QI(EDU·INC·REL·SEX)의 node
#
# 이 파일의 내용은 실행 중에 바뀌지 않는다. 고치면 온톨로지 해시가 달라지므로 새 버전으로 취급한다.

from __future__ import annotations

# ──────────────────────────────────────────────────────────────
# 국가: (ISO 3166-1 alpha-2, 영어 이름, 한국어 이름, 다른 표기들)
# 기준 집단(SynthPAI 300명)의 거주지·출생지에 나오는 국가 전부를 담았다.
# 기준 집단에는 "United States"/"USA", "United Arab Emirates"/"UAE"가 섞여 있다. 여기서 한 node로 모인다.
# ──────────────────────────────────────────────────────────────

COUNTRIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("AL", "Albania", "알바니아", ()),
    ("AR", "Argentina", "아르헨티나", ()),
    ("AM", "Armenia", "아르메니아", ()),
    ("AU", "Australia", "호주", ("오스트레일리아",)),
    ("AT", "Austria", "오스트리아", ()),
    ("AZ", "Azerbaijan", "아제르바이잔", ()),
    ("BE", "Belgium", "벨기에", ()),
    ("BO", "Bolivia", "볼리비아", ()),
    ("BR", "Brazil", "브라질", ()),
    ("BG", "Bulgaria", "불가리아", ()),
    ("CA", "Canada", "캐나다", ()),
    ("CL", "Chile", "칠레", ()),
    ("CN", "China", "중국", ()),
    ("CO", "Colombia", "콜롬비아", ()),
    ("HR", "Croatia", "크로아티아", ()),
    ("CU", "Cuba", "쿠바", ()),
    ("CZ", "Czech Republic", "체코", ("Czechia",)),
    ("DK", "Denmark", "덴마크", ()),
    ("EC", "Ecuador", "에콰도르", ()),
    ("EG", "Egypt", "이집트", ()),
    ("SV", "El Salvador", "엘살바도르", ()),
    ("ET", "Ethiopia", "에티오피아", ()),
    ("FI", "Finland", "핀란드", ()),
    ("FR", "France", "프랑스", ()),
    ("GE", "Georgia", "조지아", ()),
    ("DE", "Germany", "독일", ()),
    ("GH", "Ghana", "가나", ()),
    ("GR", "Greece", "그리스", ()),
    ("HN", "Honduras", "온두라스", ()),
    ("HU", "Hungary", "헝가리", ()),
    ("IN", "India", "인도", ()),
    ("IE", "Ireland", "아일랜드", ()),
    ("IL", "Israel", "이스라엘", ()),
    ("IT", "Italy", "이탈리아", ()),
    ("JM", "Jamaica", "자메이카", ()),
    ("JP", "Japan", "일본", ()),
    ("KE", "Kenya", "케냐", ()),
    ("LT", "Lithuania", "리투아니아", ()),
    ("MY", "Malaysia", "말레이시아", ()),
    ("MX", "Mexico", "멕시코", ()),
    ("MA", "Morocco", "모로코", ()),
    ("NL", "Netherlands", "네덜란드", ("The Netherlands", "Holland")),
    ("NZ", "New Zealand", "뉴질랜드", ()),
    ("NI", "Nicaragua", "니카라과", ()),
    ("NG", "Nigeria", "나이지리아", ()),
    ("MK", "North Macedonia", "북마케도니아", ()),
    ("NO", "Norway", "노르웨이", ()),
    ("PA", "Panama", "파나마", ()),
    ("PY", "Paraguay", "파라과이", ()),
    ("PE", "Peru", "페루", ()),
    ("PH", "Philippines", "필리핀", ("The Philippines",)),
    ("PL", "Poland", "폴란드", ()),
    ("PT", "Portugal", "포르투갈", ()),
    ("QA", "Qatar", "카타르", ()),
    ("RO", "Romania", "루마니아", ()),
    ("RU", "Russia", "러시아", ("Russian Federation",)),
    ("SA", "Saudi Arabia", "사우디아라비아", ()),
    ("RS", "Serbia", "세르비아", ()),
    ("SG", "Singapore", "싱가포르", ()),
    ("SI", "Slovenia", "슬로베니아", ()),
    ("ZA", "South Africa", "남아프리카공화국", ("남아공",)),
    ("KR", "South Korea", "대한민국", ("한국", "Korea", "Republic of Korea")),
    ("ES", "Spain", "스페인", ()),
    ("SE", "Sweden", "스웨덴", ()),
    ("CH", "Switzerland", "스위스", ()),
    ("TZ", "Tanzania", "탄자니아", ()),
    ("TH", "Thailand", "태국", ()),
    ("TN", "Tunisia", "튀니지", ()),
    ("TR", "Turkey", "튀르키예", ("터키", "Türkiye", "Turkiye")),
    ("AE", "United Arab Emirates", "아랍에미리트", ("UAE", "U.A.E.")),
    ("GB", "United Kingdom", "영국", ("UK", "U.K.", "Great Britain", "Britain")),
    ("US", "United States", "미국", ("USA", "US", "U.S.", "U.S.A.", "United States of America", "America")),
    ("UA", "Ukraine", "우크라이나", ()),
    ("UY", "Uruguay", "우루과이", ()),
    ("VE", "Venezuela", "베네수엘라", ()),
    ("ZM", "Zambia", "잠비아", ()),
)

# 국가 자리에 주(州)·구성국 이름이 온 표기 → 국가.
#   기준 집단 pers1의 출생지가 "Cleveland, Ohio"다. 응답에도 "Austin, Texas", "London, England"처럼 나올 수 있다.
#   LOC·POB 계층에는 주 단계가 없으므로(D1) 국가로만 연결한다.
#   "Georgia"는 국가(GE)이기도 하다. 그런 이름은 도시가 어느 쪽에 있는지로 가리고, 가릴 수 없으면 모호 처리한다.
_US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
)
_UK_NATIONS = ("England", "Scotland", "Wales", "Northern Ireland")

SUBDIVISION_TO_COUNTRY: dict[str, str] = {
    **{name.lower(): "US" for name in _US_STATES},
    **{name.lower(): "GB" for name in _UK_NATIONS},
}

# 기준 집단에 섞여 있는 도시 표기 갈림 (오타·현지어 표기). 표준 표기 하나로 모은다.
#   "Bejiing, China" 2명 / "Beijing, China" 9명,  "Göteborg, Sweden" 1명 / "Gothenburg, Sweden" 4명,  "Guaquil, Ecuador" 1명
CITY_ALIASES: dict[str, str] = {
    "bejiing": "Beijing",
    "goteborg": "Gothenburg",
    "guaquil": "Guayaquil",
}


# ──────────────────────────────────────────────────────────────
# EDU: (node 접미사, 부모 접미사 또는 None=ANY, 단계, 한국어, 영어)
#   D1: College·Master·PhD → Tertiary → ANY / High school → ANY
#   IN_COLLEGE(대학 재학)는 SynthPAI의 표준 단계라 node로 두되, 학위가 없으므로 Tertiary 아래에 넣지 않았다.
# ──────────────────────────────────────────────────────────────

EDU_NODES: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("TERTIARY", None, "group", "대학 이상의 학력", "tertiary education"),
    ("DOCTORATE", "TERTIARY", "level", "박사 학위", "doctorate (PhD)"),
    ("MASTER", "TERTIARY", "level", "석사 학위", "master's degree"),
    ("COLLEGE", "TERTIARY", "level", "학사 학위", "bachelor's degree"),
    ("HS", None, "level", "고등학교 졸업", "high school diploma"),
    ("IN_COLLEGE", None, "level", "대학 재학 중", "currently in college"),
)

# INC: middle이 표준 표기, medium은 다른 표기 (D1)
INC_NODES: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("LOW", None, "level", "저소득", "low income"),
    ("MIDDLE", None, "level", "중간 소득", "middle income"),
    ("HIGH", None, "level", "고소득", "high income"),
    ("VERY_HIGH", None, "level", "매우 높은 소득", "very high income"),
)

# REL: 6개 범주, 중간 단계 없음 (D1)
REL_NODES: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("SINGLE", None, "category", "미혼", "single"),
    ("IN_RELATIONSHIP", None, "category", "연애 중", "in a relationship"),
    ("ENGAGED", None, "category", "약혼", "engaged"),
    ("MARRIED", None, "category", "기혼", "married"),
    ("DIVORCED", None, "category", "이혼", "divorced"),
    ("WIDOWED", None, "category", "사별", "widowed"),
)

SEX_NODES: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("MALE", None, "category", "남성", "male"),
    ("FEMALE", None, "category", "여성", "female"),
)

CATEGORICAL_NODES = {"EDU": EDU_NODES, "INC": INC_NODES, "REL": REL_NODES, "SEX": SEX_NODES}

# AGE node를 만들어 둘 나이 범위 (기준 집단은 19~75세)
AGE_MIN = 10
AGE_MAX = 99
