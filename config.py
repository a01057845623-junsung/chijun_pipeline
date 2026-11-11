import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# 로컬 테스트용 .env 파일 로드 (GitHub Actions에서는 Secrets 환경변수 자동 주입)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SEEN_JOBS_PATH = BASE_DIR / "seen_jobs.json"

# 환경 변수 로드
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()  # "gemini" 또는 "deepseek"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()

# HTTP 요청 기본 헤더 (봇 차단 방지용 User-Agent)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 수집 대상 핵심 키워드 (포털 검색 및 게시판 1차 필터링용)
SEARCH_KEYWORDS = [
    "EAP",
    "학생상담",
    "대학상담",
    "사내상담",
    "심리상담",
    "청소년상담",
    "진로상담",
    "다인",
    "이지웰니스",
]

# 제외 키워드 (무관한 영업/단순 행정/광고 필터링용)
EXCLUDE_KEYWORDS = [
    "보험설계",
    "텔레마케터",
    "단순상담원",
    "콜센터",
    "휴대폰상담",
]


@dataclass
class TargetSource:
    """수집 대상 사이트 설정 데이터 클래스"""
    key: str
    name: str
    list_url: str
    base_url: str
    category_hint: str
    enabled: bool = True
    extra_params: dict = field(default_factory=dict)


# 주요 타깃 소스 정의
# * 각 학회/기관의 게시판 URL 변경 시 list_url만 수정하면 즉시 반영됩니다.
TARGET_SOURCES: list[TargetSource] = [
    TargetSource(
        key="krcpa",
        name="한국상담심리학회",
        list_url="https://krcpa.or.kr/user/board/boardList.do?bcd=job",
        base_url="https://krcpa.or.kr",
        category_hint="대학상담센터 / EAP 위탁기업",
    ),
    TargetSource(
        key="counselors",
        name="한국상담학회",
        list_url="https://www.counselors.or.kr/KOR/board/board.php?bo_table=sub05_03",
        base_url="https://www.counselors.or.kr",
        category_hint="대학상담센터 / 청소년·복지센터",
    ),
    TargetSource(
        key="kyci",
        name="한국청소년상담복지개발원",
        list_url="https://www.kyci.or.kr/userSite/include/board/boardList.asp?b_code=13",
        base_url="https://www.kyci.or.kr",
        category_hint="청소년/복지센터",
    ),
    TargetSource(
        key="bokji",
        name="복지넷",
        list_url="https://www.bokji.net/job/etr/01.bokji",
        base_url="https://www.bokji.net",
        category_hint="청소년/복지센터",
    ),
    TargetSource(
        key="saramin",
        name="사람인",
        list_url="https://www.saramin.co.kr/zf_user/search/recruit",
        base_url="https://www.saramin.co.kr",
        category_hint="EAP 위탁기업 / 대학상담센터",
        extra_params={"keywords": ["EAP 상담", "대학 학생상담센터", "사내상담사", "청소년상담복지센터"]},
    ),
    TargetSource(
        key="wanted",
        name="원티드",
        list_url="https://www.wanted.co.kr/api/v4/jobs",
        base_url="https://www.wanted.co.kr",
        category_hint="EAP 위탁기업",
        extra_params={"keywords": ["EAP", "심리상담", "사내상담"]},
    ),
]

# 1회 실행당 LLM 분석 및 알림을 수행할 최대 신규 공고 수 (API 쿼터 및 슬랙 스팸 방지)
MAX_NEW_JOBS_PER_RUN = int(os.getenv("MAX_NEW_JOBS_PER_RUN", "15"))
