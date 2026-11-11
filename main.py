import argparse
import logging
import sys
import time

from config import MAX_NEW_JOBS_PER_RUN, SEEN_JOBS_PATH
from crawler import JobCrawler, SeenJobsStore
from llm_extractor import LLMJobExtractor
from notifier import build_slack_block_payload, send_slack_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


def run_pipeline(dry_run: bool = False) -> int:
    """
    전체 파이프라인 실행:
    1. seen_jobs.json 로드
    2. 타깃 사이트 크롤링 및 중복 제거
    3. LLM 자격 요건 구조화 추출
    4. Slack Block Kit 알림 발송
    5. seen_jobs.json 저장
    """
    logger.info("=== 상담/EAP 채용 공고 모니터링 파이프라인 시작 (dry_run=%s) ===", dry_run)

    seen_store = SeenJobsStore(SEEN_JOBS_PATH)
    crawler = JobCrawler(seen_store)

    new_jobs = crawler.collect_all_new_jobs()
    if not new_jobs:
        logger.info("신규 채용 공고가 없습니다. 파이프라인을 종료합니다.")
        return 0

    logger.info("총 %d건의 신규 공고가 수집되었습니다. (최대 처리 한도: %d건)", len(new_jobs), MAX_NEW_JOBS_PER_RUN)
    target_jobs = new_jobs[:MAX_NEW_JOBS_PER_RUN]

    extractor = LLMJobExtractor()
    success_count = 0

    for idx, job in enumerate(target_jobs, start=1):
        logger.info("[%d/%d] LLM 분석 중: (%s) %s", idx, len(target_jobs), job.source_name, job.title)
        structured_info = extractor.extract(job)

        if dry_run:
            payload = build_slack_block_payload(structured_info, job.url)
            logger.info("[DRY-RUN] 생성된 슬랙 페이로드 헤더: %s", payload["text"])
            success_count += 1
            continue

        try:
            send_slack_notification(structured_info, job.url)
            seen_store.mark_seen(job)
            # 중간에 오류가 나더라도 이미 발송된 공고가 재발송되지 않도록 매 건 저장
            seen_store.save()
            success_count += 1
            time.sleep(1.2)  # Slack Webhook Rate Limit (초당 1건) 준수
        except Exception as e:
            logger.error("슬랙 전송 실패 [%s]: %s", job.title, e)

    logger.info("=== 파이프라인 완료: 총 %d/%d건 슬랙 알림 발송 성공 ===", success_count, len(target_jobs))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="상담/EAP 채용 공고 AI 모니터링 파이프라인")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="슬랙 발송 및 seen_jobs.json 기록 없이 수집·LLM 추출만 테스트합니다.",
    )
    args = parser.parse_args()
    sys.exit(run_pipeline(dry_run=args.dry_run))
