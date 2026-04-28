import time
import schedule
import logging
from tracker import main

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

def job():
    logger.info("정기 순위 추적을 시작합니다...")
    try:
        main()
        logger.info("정기 순위 추적이 완료되었습니다.")
    except Exception as e:
        logger.error(f"실행 중 오류 발생: {e}")

if __name__ == "__main__":
    logger.info("3시간 간격 스케줄러를 시작합니다. (이 창을 닫으면 스케줄러가 종료됩니다)")
    
    # 처음 시작할 때 한 번 실행 (원치 않으면 아래 줄 주석 처리)
    job()
    
    # 3시간마다 job 함수 실행
    schedule.every(3).hours.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(60) # 1분마다 스케줄 확인
