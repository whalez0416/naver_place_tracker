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

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)

from config import (
    TARGET_PLACE_NAME,
    TARGET_PLACE_ID,
    KEYWORDS,
    KEYWORDS_GOOGLE_ONLY,
    SLEEP_RANGE,
    PAGE_LOAD_TIMEOUT,
    LOG_FILE,
    LOG_LEVEL,
    NOTIFY_ON_BLOCK,
    NOTIFY_SUMMARY_EACH_RUN,
    MAX_RETRIES,
    RETRY_BACKOFF,
)
from anti_block import (
    create_driver,
    is_blocked,
    naver_cooldown_remaining,
    set_naver_cooldown,
    clear_naver_cooldown,
)
from sheet_io import write_to_sheet
import notifier

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
        # 구글의 접근제한/캡차는 보통 '버스트' 때문에 생기는 일시 제한이라
        # 백오프 후 재시도하면 대개 정상 결과를 얻을 수 있습니다.
        # (네이버처럼 하드 IP 차단이 아니므로 즉시 버리지 않고 MAX_RETRIES 만큼 재시도)
        loaded = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                driver.get(url)
            except WebDriverException as e:
                logger.warning(f"[구글] '{keyword}' 페이지 로드 오류: {e}")
            time.sleep(3)

            blocked_now = is_blocked(driver)
            if not blocked_now:
                # 로컬 결과 로딩 대기
                try:
                    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".VkpGBb, .cXedhc, div[data-cid]")
                        )
                    )
                    loaded = True
                    break
                except TimeoutException:
                    blocked_now = is_blocked(driver)
                    if not blocked_now:
                        # 차단이 아닌 순수 타임아웃은 재시도 의미가 적음 → 종료
                        logger.warning(f"[구글] '{keyword}' — 로딩 타임아웃")
                        return result

            # 여기 도달 = 차단/제한 감지됨
            if attempt < MAX_RETRIES:
                wait = random.uniform(*RETRY_BACKOFF)
                logger.warning(
                    f"🚫 [구글] '{keyword}' 일시 제한 감지 — {wait:.1f}초 후 재시도 "
                    f"({attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)
            else:
                logger.error(
                    f"🚫 [구글] '{keyword}' — 접근제한/캡차 (재시도 {MAX_RETRIES}회 모두 실패)"
                )
                result["blocked"] = True
                return result

        if not loaded:
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


# ── 메인 실행 ──────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("네이버 + 구글 플레이스 순위 추적기 시작")
    logger.info(f"대상 업체: {TARGET_PLACE_NAME} (ID: {TARGET_PLACE_ID})")
    logger.info(f"추적 키워드: {KEYWORDS}")
    logger.info("=" * 60)

    driver = None
    results = []

    # 직전 실행에서 네이버가 차단됐다면 쿨다운 동안 네이버를 건너뜀
    cooldown = naver_cooldown_remaining()
    naver_blocked = cooldown is not None
    if naver_blocked:
        hrs = cooldown.total_seconds() / 3600
        logger.warning(
            f"⏳ 네이버 쿨다운 중 (약 {hrs:.1f}시간 남음) — 이번 실행은 구글만 조회합니다."
        )

    block_notified = False  # 이번 실행에서 차단 알림을 이미 보냈는지

    try:
        driver = create_driver()

        # 한국어 키워드: 네이버 + 구글 모두 추적
        for i, keyword in enumerate(KEYWORDS):
            if not naver_blocked:
                naver_result = get_naver_rank(driver, keyword)
                if naver_result.get("blocked"):
                    # 한 번 차단되면 계속 두드릴수록 차단이 길어지므로 즉시 중단 + 쿨다운 기록
                    naver_blocked = True
                    set_naver_cooldown()
                    logger.warning(
                        "⚠️  네이버 차단 감지 — 남은 네이버 키워드 조회를 모두 건너뜁니다. "
                        "(구글 조회는 계속 진행)"
                    )
                    if NOTIFY_ON_BLOCK and not block_notified:
                        notifier.notify_block("네이버")
                        block_notified = True
                else:
                    results.append(naver_result)
                    # 정상 조회 성공 시 (순위 발견 여부와 무관하게) 쿨다운 해제
                    clear_naver_cooldown()
                    time.sleep(random.uniform(*SLEEP_RANGE))
            else:
                logger.info(f"[네이버] '{keyword}' — 차단/쿨다운 상태로 건너뜀")

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
            try:
                driver.quit()
                logger.info("Chrome 드라이버 종료")
            except Exception:
                pass

    changes = []
    if results:
        changes = write_to_sheet(results)

    # 결과 요약
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 결과 요약")
    logger.info("-" * 60)
    summary_lines = []
    for r in results:
        rank = r["rank"] if r["rank"] is not None else "순위권 밖"
        rank_t = r["rank_total"] if r["rank_total"] is not None else "-"
        ad = " [광고]" if r["is_ad"] else ""
        delta = r.get("delta")
        if delta:
            delta_mark = f" ({'▲' if delta > 0 else '▼'}{abs(delta)})"
        else:
            delta_mark = ""
        logger.info(
            f"  [{r['platform']}] {r['keyword']:<12s} → 광고제외 {rank}위 / 전체 {rank_t}위{ad}{delta_mark}"
        )
        summary_lines.append(
            f"[{r['platform']}] {r['keyword']}: {rank}위{delta_mark}"
        )
    logger.info("=" * 60)

    # 알림: 순위 급변 / 요약
    if changes:
        notifier.notify_rank_changes(changes)
    if NOTIFY_SUMMARY_EACH_RUN:
        notifier.notify_summary(summary_lines)


if __name__ == "__main__":
    main()
