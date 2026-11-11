import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import SLACK_WEBHOOK_URL
from llm_extractor import StructuredJobInfo

logger = logging.getLogger(__name__)

# 기관 분류별 배지 이모지 매핑
ORG_TYPE_EMOJI = {
    "대학상담센터": "🎓",
    "EAP 위탁기업": "🏢",
    "청소년/복지센터": "🌱",
    "기타": "📌",
}


def _format_bullets(items: list[str]) -> str:
    if not items:
        return "• 별도 명시 없음"
    cleaned = [item.lstrip("•-* ").strip() for item in items if item.strip()]
    if not cleaned:
        return "• 별도 명시 없음"
    return "\n".join(f"• {line}" for line in cleaned)


def build_slack_block_payload(info: StructuredJobInfo, url: str) -> dict:
    """
    StructuredJobInfo와 공고 원문 URL을 받아 Slack Block Kit 카드 페이로드를 생성합니다.
    (Slack header 블록의 최대 글자 수 제한인 150자를 안전하게 준수합니다.)
    """
    badge = ORG_TYPE_EMOJI.get(info.organization_type, "📌")
    header_text = f"{badge} [{info.organization_type}] {info.title}"
    if len(header_text) > 145:
        header_text = header_text[:142] + "..."

    req_bullets = _format_bullets(info.required_qualifications)
    pref_bullets = _format_bullets(info.preferred_qualifications)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*🏛️ 출처*\n{info.source_name}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*⏰ 접수 마감일*\n{info.deadline}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*💼 근무 형태*\n{info.employment_type}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*🏷️ 기관 분류*\n{info.organization_type}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📋 필수 자격 요건*\n{req_bullets}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⭐ 우대 사항*\n{pref_bullets}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🔗 채용 공고 원문 바로가기",
                        "emoji": True,
                    },
                    "style": "primary",
                    "url": url,
                }
            ],
        },
    ]

    return {
        "text": header_text,  # 푸시 알림 미리보기 폴백 텍스트
        "blocks": blocks,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8), reraise=True)
def send_slack_notification(info: StructuredJobInfo, url: str) -> None:
    """생성된 Slack Block Kit 카드를 Incoming Webhook으로 전송"""
    if not SLACK_WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL 환경 변수가 설정되지 않았습니다.")

    payload = build_slack_block_payload(info, url)
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info("슬랙 알림 전송 완료: [%s] %s", info.organization_type, info.title)
