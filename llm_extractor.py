import json
import logging
from typing import Literal

import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_PROVIDER,
)
from crawler import RawJobPosting

logger = logging.getLogger(__name__)


class StructuredJobInfo(BaseModel):
    """LLM이 추출하여 반환할 채용 공고 정형 JSON 스키마"""

    title: str = Field(description="공고명 (기관/기업명 포함 가능, 깔끔하게 정제된 제목)")
    organization_type: Literal["대학상담센터", "EAP 위탁기업", "청소년/복지센터", "기타"] = Field(
        description="기관 분류: 반드시 '대학상담센터', 'EAP 위탁기업', '청소년/복지센터', '기타' 중 하나로 분류"
    )
    source_name: str = Field(description="공고 출처명 (예: 한국상담심리학회, 한국상담학회, 사람인 등)")
    deadline: str = Field(
        description="접수 마감일 (예: '2026-11-30 18:00', '채용시 마감', '상시채용', '본문 확인 필요')"
    )
    required_qualifications: list[str] = Field(
        description="필수 자격 요건 (학위, 필수 자격증 급수, 필수 경력 등 핵심만 간결한 문장 배열)"
    )
    preferred_qualifications: list[str] = Field(
        description="우대 사항 (우대 자격증, 심리검사 도구 활용 능력, 관련 경험 등 간결한 문장 배열. 없으면 ['별도 명시 없음'])"
    )
    employment_type: str = Field(
        description="근무 형태 (예: 정규직, 전임상담원, 계약직, 파트타임/시간제, 객원상담원 등)"
    )


SYSTEM_PROMPT = """너는 심리상담·진로상담·EAP 채용 공고를 분석하는 전문 HR 데이터 추출 AI다.
주어진 채용 공고 원문을 읽고 다음 규칙에 따라 핵심 정보를 정확히 JSON으로 구조화하라:

1. `organization_type`은 반드시 ["대학상담센터", "EAP 위탁기업", "청소년/복지센터", "기타"] 중 하나로 분류한다.
   - 대학교 소속 학생상담센터, 인권센터, 진로심리상담센터 등은 "대학상담센터"
   - 다인, 이지웰니스, 마인드카페, 트로스트, 기업 사내상담실, EAP 전문기업 등은 "EAP 위탁기업"
   - 청소년상담복지센터, 학교밖청소년지원센터(꿈드림), Wee센터, 종합사회복지관 등은 "청소년/복지센터"
2. `required_qualifications`에는 지원에 필요한 학위(석사/학사 등), 필수 자격증(한국상담심리학회 1·2급, 전문상담사 1·2급, 청소년상담사 1·2·3급, 임상심리사 등), 필수 경력 연수를 가장 우선하여 간결하게 요약한다.
3. `preferred_qualifications`에는 우대 자격증, 심리검사(MMPI-2, TCI, 홀랜드 등) 해석 가능자, 대학/EAP 유경험자 등 우대 조건을 간결하게 요약한다.
4. 본문에 명시되지 않은 내용은 지어내지 말고 '본문 확인 필요' 또는 '별도 명시 없음'으로 표기한다.
"""


def _build_user_prompt(job: RawJobPosting) -> str:
    return (
        f"[수집 출처]: {job.source_name}\n"
        f"[카테고리 힌트]: {job.category_hint}\n"
        f"[공고 원문 제목]: {job.title}\n"
        f"[공고 링크]: {job.url}\n\n"
        f"[공고 본문 텍스트]:\n{job.raw_content}"
    )


class LLMJobExtractor:
    """Gemini 2.5 Flash 또는 DeepSeek API를 이용해 공고 본문에서 정형 JSON 추출"""

    def __init__(self):
        self.provider = LLM_PROVIDER
        if self.provider == "gemini":
            if not GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
            self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        elif self.provider == "deepseek":
            if not DEEPSEEK_API_KEY:
                raise ValueError("DEEPSEEK_API_KEY 환경 변수가 설정되지 않았습니다.")
        else:
            raise ValueError(f"지원하지 않는 LLM_PROVIDER 입니다: {self.provider}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _extract_with_gemini(self, job: RawJobPosting) -> StructuredJobInfo:
        response = self.gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=_build_user_prompt(job),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=StructuredJobInfo,
                temperature=0.1,
            ),
        )
        return StructuredJobInfo.model_validate_json(response.text)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _extract_with_deepseek(self, job: RawJobPosting) -> StructuredJobInfo:
        schema_guide = json.dumps(StructuredJobInfo.model_json_schema(), ensure_ascii=False)
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\n반드시 아래 JSON Schema 규격을 준수하여 순수 JSON 객체만 출력하라:\n{schema_guide}",
                },
                {"role": "user", "content": _build_user_prompt(job)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return StructuredJobInfo.model_validate_json(content)

    def extract(self, job: RawJobPosting) -> StructuredJobInfo:
        """공고 원문으로부터 정형화된 채용 요건 추출 (실패 시 안전한 폴백 반환)"""
        try:
            if self.provider == "deepseek":
                return self._extract_with_deepseek(job)
            return self._extract_with_gemini(job)
        except Exception as e:
            logger.error("LLM 추출 실패 [%s]: %s", job.title, e)
            return StructuredJobInfo(
                title=job.title,
                organization_type="기타",
                source_name=job.source_name,
                deadline="본문 확인 필요",
                required_qualifications=["원문 상세페이지에서 자격요건을 직접 확인해 주세요."],
                preferred_qualifications=["별도 명시 없음"],
                employment_type="본문 확인 필요",
            )
