"""
데이터 저장 모듈
- 구글 스프레드시트 기록 (순위 변동 계산 포함)
- 로컬 CSV 백업 (시트 실패 대비)
- 시트 디자인 (헤더 스타일/틀고정/줄무늬/조건부 서식)
"""

import csv
import os
import logging
from datetime import datetime

import gspread

from config import (
    CREDENTIALS_JSON_PATH,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
    TARGET_PLACE_NAME,
    CSV_BACKUP_PATH,
)

logger = logging.getLogger(__name__)

# 주의: 기존 시트 데이터(11컬럼)와의 정렬 보존을 위해 "변동"은 맨 끝에 둡니다.
HEADERS = [
    "날짜", "시간", "매체", "키워드",
    "순위(광고제외)", "순위(전체)",
    "광고여부", "업체명", "평점", "리뷰수", "검색결과수", "변동",
]

# 헤더 인덱스 (0-based)
COL_PLATFORM = 2   # 매체
COL_KEYWORD = 3    # 키워드
COL_RANK = 4       # 순위(광고제외)


def _col_letter(n: int) -> str:
    """0-based 컬럼 인덱스를 A1 표기 문자로 변환."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _parse_rank(value: str):
    """순위 문자열을 정수로. '순위권 밖'/빈값이면 None."""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


# ── 직전 순위 조회 & 변동 계산 ─────────────────────────────
def _find_prev_rank(existing_rows: list[list], platform: str, keyword: str):
    """기존 시트 데이터에서 동일 (매체, 키워드)의 가장 최근 순위를 찾습니다."""
    for row in reversed(existing_rows):
        if len(row) <= COL_RANK:
            continue
        if row[COL_PLATFORM] == platform and row[COL_KEYWORD] == keyword:
            prev = _parse_rank(row[COL_RANK])
            if prev is not None:
                return prev
    return None


def compute_changes(existing_rows: list[list], results: list[dict]) -> list[dict]:
    """각 결과에 'delta'를 채우고, 변동 목록을 반환합니다.

    delta > 0 : 순위 상승(숫자 작아짐, 좋아짐)
    delta < 0 : 순위 하락
    """
    changes = []
    for r in results:
        curr = r.get("rank")
        prev = _find_prev_rank(existing_rows, r["platform"], r["keyword"])
        delta = None
        if curr is not None and prev is not None:
            delta = prev - curr  # 양수=상승
        r["delta"] = delta
        r["prev_rank"] = prev
        if delta is not None and delta != 0:
            changes.append({
                "platform": r["platform"],
                "keyword": r["keyword"],
                "prev": prev,
                "curr": curr,
                "delta": delta,
            })
    return changes


# ── 행 구성 ────────────────────────────────────────────────
def _build_row(r: dict, date_str: str, time_str: str) -> list:
    rank_str = str(r["rank"]) if r["rank"] is not None else "순위권 밖"
    rank_total_str = str(r["rank_total"]) if r["rank_total"] is not None else "순위권 밖"
    delta = r.get("delta")
    if delta is None:
        delta_str = "-"
    elif delta > 0:
        delta_str = f"▲{delta}"
    elif delta < 0:
        delta_str = f"▼{abs(delta)}"
    else:
        delta_str = "—"
    ad_str = "O" if r["is_ad"] else "X"
    found = r["found_name"] or TARGET_PLACE_NAME
    rating = str(r.get("rating") or "")
    review_count = str(r.get("review_count") or "")
    return [
        date_str, time_str, r["platform"], r["keyword"],
        rank_str, rank_total_str,
        ad_str, found, rating, review_count, r["total_count"], delta_str,
    ]


# ── 로컬 CSV 백업 ──────────────────────────────────────────
def append_to_csv(results: list[dict], date_str: str, time_str: str) -> None:
    """결과를 로컬 CSV에 누적 저장합니다. (utf-8-sig: 엑셀 한글 호환)"""
    try:
        file_exists = os.path.exists(CSV_BACKUP_PATH)
        with open(CSV_BACKUP_PATH, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(HEADERS)
            for r in results:
                writer.writerow(_build_row(r, date_str, time_str))
        logger.info(f"로컬 CSV 백업 완료 → {CSV_BACKUP_PATH} ({len(results)}건)")
    except Exception as e:
        logger.error(f"CSV 백업 실패: {e}")


# ── 시트 디자인 ────────────────────────────────────────────
def _apply_formatting(worksheet) -> None:
    """헤더 스타일, 틀 고정, 줄무늬, 변동 컬럼 조건부 서식을 적용합니다."""
    last_col = _col_letter(len(HEADERS) - 1)
    sheet_id = worksheet.id
    try:
        # 1) 헤더 행 스타일 + 틀 고정
        worksheet.freeze(rows=1)
        worksheet.format(f"A1:{last_col}1", {
            "backgroundColor": {"red": 0.16, "green": 0.32, "blue": 0.55},
            "horizontalAlignment": "CENTER",
            "textFormat": {
                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                "bold": True,
                "fontSize": 11,
            },
        })
    except Exception as e:
        logger.warning(f"헤더 서식 적용 실패: {e}")

    # 2) 줄무늬(밴딩) + 변동 컬럼 조건부 서식
    delta_col_idx = HEADERS.index("변동")  # 0-based
    requests = [
        {
            "addBanding": {
                "bandedRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(HEADERS),
                    },
                    "rowProperties": {
                        "headerColor": {"red": 0.16, "green": 0.32, "blue": 0.55},
                        "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                        "secondBandColor": {"red": 0.93, "green": 0.95, "blue": 0.98},
                    },
                }
            }
        },
        # 변동 상승(▲)은 초록 글씨
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": delta_col_idx,
                        "endColumnIndex": delta_col_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_STARTS_WITH",
                            "values": [{"userEnteredValue": "▲"}],
                        },
                        "format": {"textFormat": {
                            "foregroundColor": {"red": 0.0, "green": 0.6, "blue": 0.2},
                            "bold": True,
                        }},
                    },
                },
                "index": 0,
            }
        },
        # 변동 하락(▼)은 빨강 글씨
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": delta_col_idx,
                        "endColumnIndex": delta_col_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_STARTS_WITH",
                            "values": [{"userEnteredValue": "▼"}],
                        },
                        "format": {"textFormat": {
                            "foregroundColor": {"red": 0.85, "green": 0.0, "blue": 0.0},
                            "bold": True,
                        }},
                    },
                },
                "index": 0,
            }
        },
    ]
    try:
        worksheet.spreadsheet.batch_update({"requests": requests})
        logger.info("시트 디자인(줄무늬/조건부서식) 적용 완료")
    except Exception as e:
        # 밴딩이 이미 있으면 오류가 날 수 있음 — 치명적이지 않으므로 경고만
        logger.warning(f"시트 디자인 적용 일부 실패(무시 가능): {e}")


# ── 메인: 시트 기록 ────────────────────────────────────────
def write_to_sheet(results: list[dict]) -> list[dict]:
    """검색 결과를 구글 시트에 기록하고 CSV 백업도 남깁니다.
    순위 변동 목록(changes)을 반환합니다. (알림용)
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    changes = []
    sheet_ok = False
    last_col = _col_letter(len(HEADERS) - 1)

    try:
        gc = gspread.service_account(filename=CREDENTIALS_JSON_PATH)
        sh = gc.open_by_key(SPREADSHEET_ID)

        try:
            worksheet = sh.worksheet(WORKSHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.sheet1
            logger.warning(f"'{WORKSHEET_NAME}' 시트 없음 — 첫 번째 시트 사용")

        existing = worksheet.get_all_values()
        existing_data = existing[1:] if existing else []

        newly_formatted = False
        if not existing:
            worksheet.append_row(HEADERS, value_input_option="RAW")
            _apply_formatting(worksheet)
            newly_formatted = True
        elif len(existing[0]) < len(HEADERS):
            # 옛 헤더 → 새 헤더로 업그레이드하며 디자인 적용
            worksheet.update(f"A1:{last_col}1", [HEADERS])
            _apply_formatting(worksheet)
            newly_formatted = True

        # 변동 계산 (기존 데이터 기준)
        changes = compute_changes(existing_data, results)

        rows = [_build_row(r, date_str, time_str) for r in results]
        worksheet.append_rows(rows, value_input_option="RAW")
        logger.info(f"구글 시트에 {len(rows)}건 기록 완료"
                    + (" (+디자인 적용)" if newly_formatted else ""))
        sheet_ok = True

    except FileNotFoundError:
        logger.error(f"credentials 파일을 찾을 수 없습니다: {CREDENTIALS_JSON_PATH}")
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error(f"스프레드시트를 찾을 수 없습니다 (ID: {SPREADSHEET_ID})")
    except Exception as e:
        logger.error(f"구글 시트 기록 중 오류: {e}", exc_info=True)

    # 시트 성공 여부와 무관하게 CSV 백업은 항상 시도
    if not changes:
        # 시트 실패로 변동 계산을 못했으면 CSV 기준으로라도 계산
        changes = _changes_from_csv(results)
    append_to_csv(results, date_str, time_str)

    if not sheet_ok:
        logger.warning("⚠️ 구글 시트 기록은 실패했지만 로컬 CSV에는 보존되었습니다.")

    return changes


def _changes_from_csv(results: list[dict]) -> list[dict]:
    """시트 접근 실패 시 CSV에서 직전 순위를 읽어 변동을 계산합니다."""
    try:
        if not os.path.exists(CSV_BACKUP_PATH):
            for r in results:
                r["delta"] = None
            return []
        with open(CSV_BACKUP_PATH, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        data = rows[1:] if rows else []
        return compute_changes(data, results)
    except Exception as e:
        logger.warning(f"CSV 기반 변동 계산 실패: {e}")
        for r in results:
            r.setdefault("delta", None)
        return []
