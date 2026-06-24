"""
네이버 + 구글 플레이스 순위 추적기
- 네이버 플레이스 전체 목록(pcmap)에서 업체 순위를 추출
- 구글 검색 지도(로컬팩)에서 업체 순위를 추출
- gspread로 구글 스프레드시트에 결과를 기록
"""

import time
import random
import logging
import re
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager
import gspread

from config import (
    CREDENTIALS_JSON_PATH,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
    TARGET_PLACE_NAME,
    TARGET_PLACE_ID,
    KEYWORDS,
    KEYWORDS_GOOGLE_ONLY,
    USER_AGENTS,
    SLEEP_RANGE,
    PAGE_LOAD_TIMEOUT,
    HEADLESS_MODE,
    LOG_FILE,
    LOG_LEVEL,
)

# ── 로깅 설정 ──────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# URL 템플릿
PCMAP_URL = "https://pcmap.place.naver.com/restaurant/list?query={keyword}"
GOOGLE_LOCAL_URL = "https://www.google.com/search?q={keyword}&udm=1"

# 차단/접근제한 페이지를 식별하는 문구 (네이버·구글 공통)
BLOCK_SIGNATURES = [
    "서비스 이용이 제한",
    "과도한 접근",
    "비정상적인 트래픽",
    "unusual traffic",
    "automated queries",
    "캡차",
    "captcha",
]


# ── 차단 페이지 감지 ───────────────────────────────────────
def is_blocked(driver: webdriver.Chrome) -> bool:
    """현재 페이지가 IP 차단/접근제한/캡차 페이지인지 판별합니다."""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    return any(sig.lower() in body_text for sig in BLOCK_SIGNATURES)


# ── Selenium 드라이버 생성 ─────────────────────────────────
def create_driver() -> webdriver.Chrome:
    """Chrome WebDriver를 생성하고 반환합니다."""
    user_agent = random.choice(USER_AGENTS)
    opts = Options()
    if HEADLESS_MODE:
        opts.add_argument("--headless=new")
    opts.add_argument(f"--user-agent={user_agent}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=ko-KR")
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


# ── 네이버: 스크롤하여 모든 항목 로딩 ─────────────────────
def scroll_to_load_all(driver: webdriver.Chrome, max_scrolls: int = 30):
    """페이지를 스크롤하여 모든 플레이스 항목을 로딩합니다."""
    scroll_container_selectors = [
        "#_pcmap_list_scroll_container",
        "#container",
        "body",
    ]

    scroll_target = None
    for sel in scroll_container_selectors:
        try:
            scroll_target = driver.find_element(By.CSS_SELECTOR, sel)
            break
        except NoSuchElementException:
            continue

    prev_count = 0
    no_change_count = 0

    for i in range(max_scrolls):
        items = driver.find_elements(By.CSS_SELECTOR, "li.UEzoS")
        if not items:
            items = driver.find_elements(By.CSS_SELECTOR, "li[data-laim-exp-id]")
        current_count = len(items)

        if current_count == prev_count:
            no_change_count += 1
            if no_change_count >= 3:
                break
        else:
            no_change_count = 0
            prev_count = current_count

        if scroll_target:
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight", scroll_target
            )
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")

        time.sleep(1.5)

    return prev_count


# ── 네이버 플레이스 순위 추출 ──────────────────────────────
def get_naver_rank(driver: webdriver.Chrome, keyword: str) -> dict:
    """네이버 플레이스 전체 목록에서 업체 순위를 찾습니다."""
    url = PCMAP_URL.format(keyword=keyword)
    logger.info(f"[네이버] 검색: '{keyword}'")

    result = {
        "platform": "네이버",
        "keyword": keyword,
        "rank": None,
        "rank_total": None,
        "is_ad": False,
        "total_count": 0,
        "found_name": None,
        "rating": None,
        "review_count": None,
        "blocked": False,
    }

    try:
        driver.get(url)
        time.sleep(3)

        # 접근제한/차단 페이지인지 먼저 확인
        if is_blocked(driver):
            logger.error(
                f"🚫 [네이버] '{keyword}' — IP 차단/접근제한 감지됨. "
                f"이번 실행의 네이버 조회를 중단합니다."
            )
            result["blocked"] = True
            return result

        try:
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#_pcmap_list_scroll_container, li.UEzoS")
                )
            )
        except TimeoutException:
            # 타임아웃 시점에 차단 페이지로 바뀌었을 수 있으므로 재확인
            if is_blocked(driver):
                logger.error(
                    f"🚫 [네이버] '{keyword}' — IP 차단/접근제한 감지됨. "
                    f"이번 실행의 네이버 조회를 중단합니다."
                )
                result["blocked"] = True
            else:
                logger.warning(f"[네이버] '{keyword}' — 로딩 타임아웃")
            return result

        total_loaded = scroll_to_load_all(driver)
        logger.info(f"[네이버] '{keyword}' — {total_loaded}개 로딩")

        items = driver.find_elements(By.CSS_SELECTOR, "li.UEzoS")
        if not items:
            items = driver.find_elements(By.CSS_SELECTOR, "li[data-laim-exp-id]")
        if not items:
            container = driver.find_elements(By.CSS_SELECTOR, "#_pcmap_list_scroll_container")
            if container:
                items = container[0].find_elements(By.TAG_NAME, "li")

        if not items:
            logger.warning(f"[네이버] '{keyword}' — 아이템 없음")
            return result

        organic_rank = 0
        for idx, item in enumerate(items, start=1):
            item_text = item.text.strip()
            if not item_text:
                continue

            # 광고 여부
            is_ad = False
            try:
                ad_els = item.find_elements(By.CSS_SELECTOR, ".gU6bV, .cZnHG")
                if ad_els:
                    is_ad = True
            except Exception:
                pass
            if not is_ad and ("광고" in item_text[:20]):
                is_ad = True

            if not is_ad:
                organic_rank += 1

            # 업체명 추출
            place_name = ""
            for ns in [".TYU6d", ".YwYLL", ".place_bluelink", ".t3FNS", "a.tzwk0", "span.YTJkH"]:
                try:
                    el = item.find_element(By.CSS_SELECTOR, ns)
                    place_name = el.text.strip()
                    if place_name:
                        break
                except NoSuchElementException:
                    continue
            if not place_name:
                place_name = item_text.split("\n")[0].strip()

            # 매칭 확인
            matched = False
            if TARGET_PLACE_ID:
                try:
                    for link in item.find_elements(By.TAG_NAME, "a"):
                        href = link.get_attribute("href") or ""
                        if TARGET_PLACE_ID in href:
                            matched = True
                            break
                except Exception:
                    pass

            if not matched and TARGET_PLACE_NAME:
                if TARGET_PLACE_NAME in place_name:
                    matched = True

            if matched:
                # 별점 및 리뷰 수 추출
                single_line_text = item_text.replace("\n", " ")
                rating_match = re.search(r'별점\s*([0-9.]+)', single_line_text)
                review_match = re.search(r'리뷰\s*([0-9.,만]+)', single_line_text)
                
                result["rank"] = organic_rank if not is_ad else None
                result["rank_total"] = idx
                result["is_ad"] = is_ad
                result["found_name"] = place_name
                if rating_match:
                    result["rating"] = rating_match.group(1)
                if review_match:
                    result["review_count"] = review_match.group(1)
                    
                logger.info(
                    f"✅ [네이버] '{keyword}' — '{place_name}' "
                    f"전체 {idx}위 / 광고제외 {organic_rank}위"
                )
                break

        result["total_count"] = organic_rank
        if result["rank"] is None and result["rank_total"] is None:
            logger.info(f"❌ [네이버] '{keyword}' — {organic_rank}개 내 미발견")

    except WebDriverException as e:
        logger.error(f"[네이버] '{keyword}' WebDriver 오류: {e}")
    except Exception as e:
        logger.error(f"[네이버] '{keyword}' 오류: {e}", exc_info=True)

    return result


# ── 구글 지도(로컬팩) 순위 추출 ────────────────────────────
def get_google_rank(driver: webdriver.Chrome, keyword: str) -> dict:
    """구글 검색 지도(로컬팩) 전체 목록에서 업체 순위를 찾습니다."""
    url = GOOGLE_LOCAL_URL.format(keyword=keyword)
    logger.info(f"[구글] 검색: '{keyword}'")

    result = {
        "platform": "구글",
        "keyword": keyword,
        "rank": None,
        "rank_total": None,
        "is_ad": False,
        "total_count": 0,
        "found_name": None,
        "rating": None,
        "review_count": None,
        "blocked": False,
    }

    try:
        driver.get(url)
        time.sleep(3)

        # 접근제한/캡차 페이지인지 먼저 확인
        if is_blocked(driver):
            logger.error(f"🚫 [구글] '{keyword}' — 접근제한/캡차 감지됨.")
            result["blocked"] = True
            return result

        # 로컬 결과 로딩 대기
        try:
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".VkpGBb, .cXedhc, div[data-cid]")
                )
            )
        except TimeoutException:
            if is_blocked(driver):
                logger.error(f"🚫 [구글] '{keyword}' — 접근제한/캡차 감지됨.")
                result["blocked"] = True
            else:
                logger.warning(f"[구글] '{keyword}' — 로딩 타임아웃")
            return result

        # 페이지네이션으로 전체 결과 탐색 (최대 5페이지 = 100개)
        max_pages = 5
        overall_rank = 0
        found = False

        for page in range(max_pages):
            time.sleep(2)

            # 개별 업체 항목 가져오기
            items = driver.find_elements(By.CSS_SELECTOR, ".VkpGBb")
            if not items:
                items = driver.find_elements(By.CSS_SELECTOR, ".cXedhc")
            if not items:
                items = driver.find_elements(By.CSS_SELECTOR, "div[data-cid]")

            if not items:
                logger.debug(f"[구글] 페이지 {page + 1} — 아이템 없음")
                break

            for idx, item in enumerate(items, start=1):
                item_text = item.text.strip()
                if not item_text:
                    continue

                overall_rank += 1

                # 광고 여부
                is_ad = False
                if "광고" in item_text[:10] or "Ad" in item_text[:10]:
                    is_ad = True

                # 업체명 추출
                place_name = ""
                for ns in [".dbg0pd", ".OSrXXb", ".rgnuSb"]:
                    try:
                        el = item.find_element(By.CSS_SELECTOR, ns)
                        place_name = el.text.strip()
                        if place_name:
                            break
                    except NoSuchElementException:
                        continue
                if not place_name:
                    place_name = item_text.split("\n")[0].strip()

                # 매칭 확인
                if TARGET_PLACE_NAME and TARGET_PLACE_NAME in place_name:
                    # 구글 평점 및 리뷰 수 추출 (예: 4.5(1,234))
                    google_match = re.search(r'([0-9]\.[0-9])\(([0-9,]+)\)', item_text.replace(" ", ""))
                    
                    result["rank"] = overall_rank
                    result["rank_total"] = overall_rank
                    result["is_ad"] = is_ad
                    result["found_name"] = place_name
                    if google_match:
                        result["rating"] = google_match.group(1)
                        result["review_count"] = google_match.group(2)
                        
                    logger.info(
                        f"✅ [구글] '{keyword}' — '{place_name}' {overall_rank}위 (페이지 {page + 1})"
                    )
                    found = True
                    break

            if found:
                break

            # 다음 페이지 버튼 클릭
            try:
                next_btn = driver.find_element(By.CSS_SELECTOR, 'a#pnnext, div[aria-label="다음"][role="button"], td.navend a')
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(2)
            except (NoSuchElementException, WebDriverException):
                logger.debug(f"[구글] 마지막 페이지 도달 (페이지 {page + 1})")
                break

        result["total_count"] = overall_rank
        if not found:
            logger.info(f"❌ [구글] '{keyword}' — {overall_rank}개 내 미발견")

    except WebDriverException as e:
        logger.error(f"[구글] '{keyword}' WebDriver 오류: {e}")
    except Exception as e:
        logger.error(f"[구글] '{keyword}' 오류: {e}", exc_info=True)

    return result


# ── 구글 스프레드시트 기록 ──────────────────────────────────
def write_to_sheet(results: list[dict]):
    """검색 결과를 구글 스프레드시트에 기록합니다."""
    try:
        gc = gspread.service_account(filename=CREDENTIALS_JSON_PATH)
        sh = gc.open_by_key(SPREADSHEET_ID)

        try:
            worksheet = sh.worksheet(WORKSHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.sheet1
            logger.warning(f"'{WORKSHEET_NAME}' 시트 없음 — 첫 번째 시트 사용")

        existing = worksheet.get_all_values()
        headers = ["날짜", "시간", "매체", "키워드", "순위(광고제외)", "순위(전체)", "광고여부", "업체명", "평점", "리뷰수", "검색결과수"]
        
        if not existing:
            worksheet.append_row(headers, value_input_option="RAW")
        elif len(existing[0]) < len(headers):
            # 기존 헤더가 옛날 버전인 경우 업데이트
            worksheet.update('A1:K1', [headers])

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        rows = []
        for r in results:
            rank_str = str(r["rank"]) if r["rank"] is not None else "순위권 밖"
            rank_total_str = str(r["rank_total"]) if r["rank_total"] is not None else "순위권 밖"
            ad_str = "O" if r["is_ad"] else "X"
            found = r["found_name"] or TARGET_PLACE_NAME
            rating = str(r.get("rating") or "")
            review_count = str(r.get("review_count") or "")
            
            rows.append([
                date_str, time_str, r["platform"], r["keyword"],
                rank_str, rank_total_str, ad_str, found, rating, review_count, r["total_count"],
            ])

        worksheet.append_rows(rows, value_input_option="RAW")
        logger.info(f"구글 시트에 {len(rows)}건 기록 완료")

    except FileNotFoundError:
        logger.error(f"credentials 파일을 찾을 수 없습니다: {CREDENTIALS_JSON_PATH}")
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error(f"스프레드시트를 찾을 수 없습니다 (ID: {SPREADSHEET_ID})")
    except Exception as e:
        logger.error(f"구글 시트 기록 중 오류: {e}", exc_info=True)


# ── 메인 실행 ──────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("네이버 + 구글 플레이스 순위 추적기 시작")
    logger.info(f"대상 업체: {TARGET_PLACE_NAME} (ID: {TARGET_PLACE_ID})")
    logger.info(f"추적 키워드: {KEYWORDS}")
    logger.info("=" * 60)

    driver = None
    results = []
    naver_blocked = False  # 네이버 차단 감지 시 남은 네이버 조회를 건너뜀

    try:
        driver = create_driver()

        # 한국어 키워드: 네이버 + 구글 모두 추적
        for i, keyword in enumerate(KEYWORDS):
            if not naver_blocked:
                naver_result = get_naver_rank(driver, keyword)
                if naver_result.get("blocked"):
                    # 한 번 차단되면 계속 두드릴수록 차단이 길어지므로 즉시 중단
                    naver_blocked = True
                    logger.warning(
                        "⚠️  네이버 차단 감지 — 남은 네이버 키워드 조회를 모두 건너뜁니다. "
                        "(구글 조회는 계속 진행)"
                    )
                else:
                    results.append(naver_result)
                    time.sleep(random.uniform(*SLEEP_RANGE))
            else:
                logger.info(f"[네이버] '{keyword}' — 차단 상태로 건너뜀")

            google_result = get_google_rank(driver, keyword)
            if not google_result.get("blocked"):
                results.append(google_result)

            wait = random.uniform(*SLEEP_RANGE)
            logger.info(f"다음 키워드까지 {wait:.1f}초 대기...")
            time.sleep(wait)

        # 영어 키워드: 구글만 추적 (외국인 관광객용)
        logger.info("--- 구글 전용 키워드 (외국인 관광객) ---")
        for i, keyword in enumerate(KEYWORDS_GOOGLE_ONLY):
            google_result = get_google_rank(driver, keyword)
            if not google_result.get("blocked"):
                results.append(google_result)

            if i < len(KEYWORDS_GOOGLE_ONLY) - 1:
                wait = random.uniform(*SLEEP_RANGE)
                logger.info(f"다음 키워드까지 {wait:.1f}초 대기...")
                time.sleep(wait)

    except Exception as e:
        logger.error(f"크롤링 중 치명적 오류: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()
            logger.info("Chrome 드라이버 종료")

    if results:
        write_to_sheet(results)

    # 결과 요약
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 결과 요약")
    logger.info("-" * 60)
    for r in results:
        rank = r["rank"] if r["rank"] is not None else "순위권 밖"
        rank_t = r["rank_total"] if r["rank_total"] is not None else "-"
        ad = " [광고]" if r["is_ad"] else ""
        logger.info(f"  [{r['platform']}] {r['keyword']:<12s} → 광고제외 {rank}위 / 전체 {rank_t}위{ad}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
