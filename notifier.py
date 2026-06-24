"""
알림 모듈 (텔레그램)
- 설정(TELEGRAM_BOT_TOKEN/CHAT_ID)이 없으면 조용히 무시합니다.
- 순위 급변, IP 차단, 실행 요약 알림에 사용합니다.
"""

import logging

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    NOTIFY_RANK_CHANGE_THRESHOLD,
)

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send(message: str) -> bool:
    """텔레그램으로 메시지를 전송합니다. 미설정 시 False를 반환합니다."""
    if not is_configured():
        logger.debug("텔레그램 미설정 — 알림 생략")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("텔레그램 알림 전송 완료")
            return True
        logger.warning(f"텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"텔레그램 전송 오류: {e}")
        return False


def notify_block(platform: str) -> None:
    """IP 차단 감지 알림."""
    send(
        f"🚫 <b>{platform} 차단 감지</b>\n"
        f"과도한 접근으로 접근이 제한되었습니다. "
        f"해당 플랫폼 조회를 쿨다운 동안 건너뜁니다."
    )


def notify_rank_changes(changes: list[dict]) -> None:
    """순위 급변 알림.

    changes: [{"platform","keyword","prev","curr","delta"}, ...]
    delta > 0 = 순위 상승(좋아짐), delta < 0 = 하락.
    임계값 미만은 호출 측에서 걸러서 넘기는 것을 권장하지만, 여기서도 한 번 더 거릅니다.
    """
    significant = [
        c for c in changes
        if c.get("delta") is not None
        and abs(c["delta"]) >= NOTIFY_RANK_CHANGE_THRESHOLD
    ]
    if not significant:
        return

    lines = ["📊 <b>순위 변동 알림</b>"]
    for c in significant:
        delta = c["delta"]
        arrow = "🔺" if delta > 0 else "🔻"
        direction = "상승" if delta > 0 else "하락"
        lines.append(
            f"{arrow} [{c['platform']}] {c['keyword']}: "
            f"{c['prev']}위 → {c['curr']}위 ({abs(delta)}계단 {direction})"
        )
    send("\n".join(lines))


def notify_summary(summary_lines: list[str]) -> None:
    """실행 요약 알림."""
    if not summary_lines:
        return
    send("📋 <b>순위 추적 완료</b>\n" + "\n".join(summary_lines))
