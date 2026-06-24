# ============================================================
# 네이버 플레이스 순위 추적기 - 설정 파일
# ============================================================
# 이 파일에서 모든 주요 설정을 관리합니다.
# 아래 값을 본인의 환경에 맞게 수정하세요.
# ============================================================

# --- Google Sheets 설정 ---
# Google Cloud 서비스 계정 credentials JSON 파일 경로
CREDENTIALS_JSON_PATH = "naver-place-tracker-2b48f6ba8e6d.json"

# Google 스프레드시트 ID (URL에서 확인 가능)
# 예: https://docs.google.com/spreadsheets/d/여기가_스프레드시트_ID/edit
SPREADSHEET_ID = "1UoS5VprVDIy-fTfAq0As4F624XUQ7_dUPNNAEbUj99k"

# 데이터를 기록할 워크시트 이름 (기본: 첫 번째 시트)
WORKSHEET_NAME = "Sheet1"


# --- 추적 대상 설정 ---
# 순위를 추적할 업체명 (네이버 플레이스에 등록된 정확한 이름)
TARGET_PLACE_NAME = "헤르지아"

# 업체의 네이버 플레이스 ID (선택사항, 더 정확한 매칭을 위해 사용)
# 네이버 지도에서 업체를 검색한 후 URL에서 확인 가능
# 예: https://map.naver.com/v5/entry/place/1234567890
# 비워두면 업체명으로만 매칭합니다.
TARGET_PLACE_ID = "12780405"


# --- 검색 키워드 목록 ---
# 네이버 + 구글 모두 추적할 키워드 (한국어)
KEYWORDS = [
    "남산돈까스",
    "남산데이트",
    "남산맛집",
    "명동맛집",
]

# 구글만 추적할 키워드 (외국인 관광객용 - 검색량 기준)
KEYWORDS_GOOGLE_ONLY = [
    "Myeongdong restaurant",
    "Myeongdong food",
    "best restaurant in Myeongdong",
    "Namsan Tower restaurant",
    "Seoul tonkatsu",
]


# --- Selenium / 크롤링 설정 ---
# 네이버 플레이스 전체 목록 URL (pcmap 사용 - tracker.py에서 자동 처리)
# 수정할 필요 없음

# User-Agent 목록 (랜덤으로 선택하여 차단 방지)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# 각 검색 사이의 대기 시간 (초) - 랜덤 범위 [최소, 최대]
# 너무 짧으면 네이버/구글에서 "과도한 접근"으로 IP가 차단될 수 있음
SLEEP_RANGE = (8, 16)

# 페이지 로딩 대기 시간 (초)
PAGE_LOAD_TIMEOUT = 15

# 플레이스 섹션 최대 검색 페이지 수
# 네이버 플레이스 섹션은 보통 5페이지(25개) 정도 표시됨
MAX_PLACE_PAGES = 5

# 헤드리스 모드 (True: 브라우저 창 안 보임, False: 브라우저 창 표시)
HEADLESS_MODE = True


# --- 로깅 설정 ---
# 로그 파일 경로
LOG_FILE = "rank_tracker.log"

# 로그 레벨 ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_LEVEL = "INFO"
