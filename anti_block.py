"""
차단 회피 모듈
- Chrome WebDriver 생성 (탐지 회피 옵션 / undetected-chromedriver 선택)
- 차단·접근제한·캡차 페이지 감지
- 차단 쿨다운 상태를 파일에 저장/조회 (실행 간 유지)
"""

import json
import random
import logging
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from config import (
    USER_AGENTS,
    HEADLESS_MODE,
    PAGE_LOAD_TIMEOUT,
    USE_UNDETECTED_CHROMEDRIVER,
    COOLDOWN_ESCALATION_MAX,
    STATE_FILE,
)

logger = logging.getLogger(__name__)

# 차단/접근제한/캡차 페이지를 식별하는 문구 (네이버·구글 공통)
BLOCK_SIGNATURES = [
    "서비스 이용이 제한",
    "과도한 접근",
    "비정상적인 트래픽",
    "unusual traffic",
    "automated queries",
    "캡차",
    "captcha",
    "정상적인 접근",
    "보안문자",
]


# ── 드라이버 생성 ──────────────────────────────────────────
def _chrome_options(user_agent: str) -> Options:
    opts = Options()
    if HEADLESS_MODE:
        opts.add_argument("--headless=new")
    opts.add_argument(f"--user-agent={user_agent}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # 창 크기를 매번 살짝 다르게 (지문 변화)
    width = random.choice([1920, 1680, 1600, 1536])
    height = random.choice([1080, 1050, 960, 900])
    opts.add_argument(f"--window-size={width},{height}")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--disable-gpu")
    return opts


def create_driver() -> webdriver.Chrome:
    """Chrome WebDriver를 생성합니다. 탐지 회피 옵션을 적용합니다."""
    user_agent = random.choice(USER_AGENTS)

    # 1) undetected-chromedriver 우선 시도 (설정 ON & 설치된 경우)
    if USE_UNDETECTED_CHROMEDRIVER:
        try:
            import undetected_chromedriver as uc

            uc_opts = uc.ChromeOptions()
            if HEADLESS_MODE:
                uc_opts.add_argument("--headless=new")
            uc_opts.add_argument(f"--user-agent={user_agent}")
            uc_opts.add_argument("--lang=ko-KR")
            uc_opts.add_argument("--no-sandbox")
            uc_opts.add_argument("--disable-dev-shm-usage")
            driver = uc.Chrome(options=uc_opts)
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT * 3)
            logger.info("undetected-chromedriver로 드라이버 생성 완료")
            return driver
        except ImportError:
            logger.warning(
                "undetected-chromedriver가 설치되지 않아 일반 드라이버로 대체합니다. "
                "(pip install undetected-chromedriver)"
            )
        except Exception as e:
            logger.warning(f"undetected-chromedriver 생성 실패({e}) — 일반 드라이버로 대체합니다.")

    # 2) 일반 Selenium 드라이버
    opts = _chrome_options(user_agent)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT * 3)
    logger.info(f"Chrome 드라이버 생성 완료 (UA: {user_agent[:50]}...)")
    return driver


# ── 차단 페이지 감지 ───────────────────────────────────────
def is_blocked(driver: webdriver.Chrome) -> bool:
    """현재 페이지가 IP 차단/접근제한/캡차 페이지인지 판별합니다."""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    return any(sig.lower() in body_text for sig in BLOCK_SIGNATURES)


# ── 차단 쿨다운 상태 관리 ──────────────────────────────────
def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"상태 파일 읽기 실패: {e}")
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"상태 파일 저장 실패: {e}")


def cooldown_remaining(platform: str) -> timedelta | None:
    """플랫폼('naver'/'google') 쿨다운이 남아있으면 남은 시간을, 아니면 None을 반환합니다."""
    state = _load_state()
    until_str = state.get(f"{platform}_blocked_until")
    if not until_str:
        return None
    try:
        until = datetime.fromisoformat(until_str)
    except ValueError:
        return None
    now = datetime.now()
    if until > now:
        return until - now
    return None


def set_cooldown(platform: str, base_hours: float) -> datetime:
    """플랫폼 차단을 기록하고 쿨다운 종료 시각을 저장합니다.

    반복 차단 시 쿨다운을 점진적으로 늘립니다.
    실제 시간 = base_hours × min(2^(연속차단-1), COOLDOWN_ESCALATION_MAX)
    정상 조회에 성공하면 clear_cooldown()이 연속차단 카운트를 리셋합니다.
    """
    state = _load_state()
    count_key = f"{platform}_block_count"
    count = int(state.get(count_key, 0)) + 1
    multiplier = min(2 ** (count - 1), COOLDOWN_ESCALATION_MAX)
    hours = base_hours * multiplier

    until = datetime.now() + timedelta(hours=hours)
    state[f"{platform}_blocked_until"] = until.isoformat(timespec="seconds")
    state[count_key] = count
    state["last_block_detected"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)

    escalated = " (반복 차단으로 연장)" if count > 1 else ""
    logger.warning(
        f"{platform} 쿨다운 설정됨 — {until.strftime('%Y-%m-%d %H:%M')}까지 "
        f"(약 {hours:.0f}시간, 연속 {count}회 차단){escalated} 조회를 건너뜁니다."
    )
    return until


def clear_cooldown(platform: str) -> None:
    """플랫폼 쿨다운과 연속차단 카운트를 해제합니다 (정상 조회 성공 시 호출)."""
    state = _load_state()
    changed = state.pop(f"{platform}_blocked_until", None) is not None
    changed = state.pop(f"{platform}_block_count", None) is not None or changed
    if changed:
        _save_state(state)
        logger.info(f"{platform} 쿨다운 해제됨 (정상 조회 확인)")
