import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup

from config import (
    DEFAULT_HEADERS,
    EXCLUDE_KEYWORDS,
    SEARCH_KEYWORDS,
    SEEN_JOBS_PATH,
    TARGET_SOURCES,
    TargetSource,
)

logger = logging.getLogger(__name__)


@dataclass
class RawJobPosting:
    """크롤러가 1차 수집한 원시 채용 공고 데이터"""
    job_id: str
    source_name: str
    title: str
    url: str
    raw_content: str
    category_hint: str = "기타"


class SeenJobsStore:
    """이미 수집·발송한 공고를 seen_jobs.json으로 영구 관리하는 스토어"""

    def __init__(self, file_path: Path = SEEN_JOBS_PATH):
        self.file_path = file_path
        self.seen: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.file_path.exists():
            return {}
        try:
            with self.file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning("seen_jobs.json 로드 실패 (초기화 진행): %s", e)
            return {}

    def is_seen(self, job_id: str) -> bool:
        return job_id in self.seen

    def mark_seen(self, job: RawJobPosting) -> None:
        self.seen[job.job_id] = {
            "title": job.title,
            "source_name": job.source_name,
            "url": job.url,
            "notified_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        # 파일 크기가 무한정 커지는 것을 방지하기 위해 최근 2,000건만 유지
        if len(self.seen) > 2000:
            sorted_items = sorted(
                self.seen.items(),
                key=lambda item: item[1].get("notified_at", ""),
                reverse=True,
            )[:2000]
            self.seen = dict(sorted_items)

        with self.file_path.open("w", encoding="utf-8") as f:
            json.dump(self.seen, f, ensure_ascii=False, indent=2)


def make_job_id(source_key: str, unique_str: str) -> str:
    """출처 키와 고유 식별자(URL 또는 게시글 번호)를 조합해 SHA-256 해시 ID 생성"""
    normalized = f"{source_key}:{unique_str.strip()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{source_key}_{digest}"


def is_relevant_title(title: str, strict_keyword: bool = False) -> bool:
    """공고 제목 기반 1차 필터링 (제외 키워드 차단 및 검색 키워드 매칭)"""
    clean = title.strip()
    if len(clean) < 4:
        return False

    for ex in EXCLUDE_KEYWORDS:
        if ex.lower() in clean.lower():
            return False

    if not strict_keyword:
        return True

    return any(kw.lower() in clean.lower() for kw in SEARCH_KEYWORDS)


def clean_html_text(html: str, max_chars: int = 6000) -> str:
    """HTML에서 불필요한 스크립트/스타일/메뉴를 제거하고 본문 텍스트만 정제"""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "iframe", "svg"]):
        tag.decompose()

    # 본문 컨테이너 우선 탐색
    main_Selectors = [
        "div.board_view",
        "div.view_cont",
        "div.jv_cont",
        "div.user_content",
        "div#bo_v_con",
        "td.board_contents",
        "article",
        "main",
    ]
    target_node = None
    for sel in main_Selectors:
        found = soup.select_one(sel)
        if found and len(found.get_text(strip=True)) > 80:
            target_node = found
            break

    text = (target_node or soup).get_text(separator="\n", strip=True)
    # 다중 공백 및 빈 줄 정리
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    cleaned = "\n".join(lines)
    return cleaned[:max_chars]


class JobCrawler:
    """타깃 사이트별 채용 공고 수집기"""

    def __init__(self, seen_store: SeenJobsStore):
        self.seen_store = seen_store
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _fetch_html(self, url: str, params: dict | None = None) -> str | None:
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            # 한글 인코딩 자동 보정 (EUC-KR / CP949 사이트 대응)
            if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "ascii"):
                resp.encoding = resp.apparent_encoding
            return resp.text
        except Exception as e:
            logger.warning("HTTP 요청 실패 [%s]: %s", url, e)
            return None

    def fetch_detail_text(self, url: str, fallback_title: str = "") -> str:
        """상세 페이지 URL에 접속해 공고 본문 텍스트 추출"""
        html = self._fetch_html(url)
        if not html:
            return f"공고명: {fallback_title}\n상세 원문 링크: {url}"
        text = clean_html_text(html)
        if len(text) < 40:
            return f"공고명: {fallback_title}\n{text}\n상세 원문 링크: {url}"
        return text

    def crawl_generic_board(
        self,
        source: TargetSource,
        strict_keyword: bool = False,
        max_items: int = 10,
    ) -> list[RawJobPosting]:
        """
        학회/기관 구인 게시판(한국상담심리학회, 한국상담학회, 청소년상담복지개발원, 복지넷)의
        표준 테이블/리스트 구조를 파싱하여 신규 공고 목록을 수집합니다.
        """
        html = self._fetch_html(source.list_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        candidates: list[tuple[str, str]] = []
        seen_urls: set[str] = set()

        # 게시판 내 <a> 태그 탐색 (일반적인 구인 게시판의 상세 링크 패턴)
        for a_tag in soup.select("table tbody tr a, ul.board_list li a, div.board_list a, td.subject a, td.title a, a"):
            href = (a_tag.get("href") or "").strip()
            title = a_tag.get_text(" ", strip=True)

            if not href or href.startswith(("#", "javascript:void")):
                continue

            # javascript:fnView('1234') 형태 처리
            js_match = re.search(r"['\"](\d{3,10})['\"]", href)
            if href.startswith("javascript:") and js_match:
                seq = js_match.group(1)
                full_url = f"{source.list_url}&seq={seq}" if "?" in source.list_url else f"{source.list_url}?seq={seq}"
            else:
                full_url = urljoin(source.base_url, href)

            # 네비게이션/메뉴 링크 제외 및 게시글 상세 링크 필터링
            if full_url == source.list_url or full_url in seen_urls:
                continue
            if not any(
                token in full_url.lower()
                for token in ["view", "read", "detail", "seq=", "wr_id=", "idx=", "no=", "ntt", "etr", "job", "board"]
            ):
                continue

            if not is_relevant_title(title, strict_keyword=strict_keyword):
                continue

            seen_urls.add(full_url)
            candidates.append((title, full_url))
            if len(candidates) >= max_items:
                break

        results: list[RawJobPosting] = []
        for title, full_url in candidates:
            job_id = make_job_id(source.key, full_url)
            if self.seen_store.is_seen(job_id):
                continue

            time.sleep(0.7)  # 대상 서버 부하 방지 딜레이
            raw_content = self.fetch_detail_text(full_url, fallback_title=title)
            results.append(
                RawJobPosting(
                    job_id=job_id,
                    source_name=source.name,
                    title=title,
                    url=full_url,
                    raw_content=raw_content,
                    category_hint=source.category_hint,
                )
            )

        logger.info("[%s] 신규 공고 %d건 감지", source.name, len(results))
        return results

    def crawl_saramin(self, source: TargetSource, max_per_keyword: int = 5) -> list[RawJobPosting]:
        """사람인 키워드 검색 결과 수집 ('EAP 상담', '대학 학생상담센터', '사내상담사' 등)"""
        keywords = source.extra_params.get("keywords", SEARCH_KEYWORDS[:4])
        results: list[RawJobPosting] = []
        local_seen: set[str] = set()

        for kw in keywords:
            params = {
                "searchType": "search",
                "searchword": kw,
                "recruitSort": "reg_dt",  # 최신 등록순 정렬
                "recruitPageCount": 15,
            }
            html = self._fetch_html(source.list_url, params=params)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")
            items = soup.select("div.item_recruit")
            count = 0

            for item in items:
                title_el = item.select_one("h2.job_tit a")
                corp_el = item.select_one("div.area_corp strong.corp_name a")
                cond_el = item.select_one("div.job_condition")
                date_el = item.select_one("div.job_date span.date")
                sector_el = item.select_one("div.job_sector")

                if not title_el:
                    continue

                raw_title = title_el.get_text(" ", strip=True)
                corp_name = corp_el.get_text(" ", strip=True) if corp_el else ""
                full_title = f"[{corp_name}] {raw_title}" if corp_name else raw_title

                if not is_relevant_title(full_title, strict_keyword=True):
                    continue

                href = title_el.get("href", "")
                full_url = urljoin(source.base_url, href)

                # rec_idx 고유 공고 번호 추출
                parsed = parse_qs(urlparse(full_url).query)
                rec_idx = parsed.get("rec_idx", [full_url])[0]
                canonical_url = f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={rec_idx}"

                job_id = make_job_id(source.key, str(rec_idx))
                if self.seen_store.is_seen(job_id) or job_id in local_seen:
                    continue
                local_seen.add(job_id)

                # 검색 목록 메타데이터 + 상세 뷰 요약 결합
                meta_summary = "\n".join(
                    filter(
                        None,
                        [
                            f"기업/기관명: {corp_name}",
                            f"공고명: {raw_title}",
                            f"근무조건 요약: {cond_el.get_text(' | ', strip=True) if cond_el else ''}",
                            f"마감일 표시: {date_el.get_text(' ', strip=True) if date_el else ''}",
                            f"직무분야: {sector_el.get_text(' ', strip=True) if sector_el else ''}",
                        ],
                    )
                )

                time.sleep(0.7)
                detail_text = self.fetch_detail_text(canonical_url, fallback_title=full_title)
                combined_content = f"{meta_summary}\n\n[상세 본문]\n{detail_text}"

                results.append(
                    RawJobPosting(
                        job_id=job_id,
                        source_name=f"{source.name} ({kw})",
                        title=full_title,
                        url=canonical_url,
                        raw_content=combined_content[:6000],
                        category_hint=source.category_hint,
                    )
                )
                count += 1
                if count >= max_per_keyword:
                    break

        logger.info("[%s] 신규 공고 %d건 감지", source.name, len(results))
        return results

    def crawl_wanted(self, source: TargetSource, max_per_keyword: int = 5) -> list[RawJobPosting]:
        """원티드 공개 검색 API(v4)를 활용한 EAP/사내상담 포지션 수집"""
        keywords = source.extra_params.get("keywords", ["EAP", "심리상담", "사내상담"])
        results: list[RawJobPosting] = []
        local_seen: set[str] = set()

        for kw in keywords:
            search_url = "https://www.wanted.co.kr/api/chaos/search/v1/position"
            params = {"query": kw, "country": "kr", "limit": 10, "offset": 0}
            try:
                resp = self.session.get(search_url, params=params, timeout=15)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
            except Exception as e:
                logger.warning("[원티드] 검색 API 호출 실패 (%s): %s", kw, e)
                continue

            positions = payload.get("data", []) if isinstance(payload.get("data"), list) else []
            count = 0

            for pos in positions:
                pos_id = str(pos.get("id", ""))
                position_name = pos.get("position", "")
                company_name = (pos.get("company") or {}).get("name", "")
                full_title = f"[{company_name}] {position_name}" if company_name else position_name

                if not pos_id or not is_relevant_title(full_title, strict_keyword=True):
                    continue

                job_id = make_job_id(source.key, pos_id)
                if self.seen_store.is_seen(job_id) or job_id in local_seen:
                    continue
                local_seen.add(job_id)

                detail_api_url = f"https://www.wanted.co.kr/api/v4/jobs/{pos_id}"
                canonical_url = f"https://www.wanted.co.kr/wd/{pos_id}"
                raw_content = f"기업명: {company_name}\n포지션: {position_name}\n"

                try:
                    time.sleep(0.5)
                    d_resp = self.session.get(detail_api_url, timeout=15)
                    if d_resp.status_code == 200:
                        job_detail = d_resp.json().get("job", {})
                        detail_obj = job_detail.get("detail", {})
                        due_time = job_detail.get("due_time") or "상시채용"
                        raw_content += (
                            f"마감일: {due_time}\n"
                            f"[주요업무]\n{detail_obj.get('main_tasks', '')}\n"
                            f"[자격요건]\n{detail_obj.get('requirements', '')}\n"
                            f"[우대사항]\n{detail_obj.get('preferred_points', '')}\n"
                        )
                except Exception as e:
                    logger.warning("[원티드] 상세 API 조회 실패 (%s): %s", pos_id, e)

                results.append(
                    RawJobPosting(
                        job_id=job_id,
                        source_name=source.name,
                        title=full_title,
                        url=canonical_url,
                        raw_content=raw_content[:6000],
                        category_hint="EAP 위탁기업",
                    )
                )
                count += 1
                if count >= max_per_keyword:
                    break

        logger.info("[%s] 신규 공고 %d건 감지", source.name, len(results))
        return results

    def collect_all_new_jobs(self) -> list[RawJobPosting]:
        """설정된 모든 타깃 소스를 순회하며 미발송 신규 공고 리스트 반환"""
        all_new_jobs: list[RawJobPosting] = []

        for source in TARGET_SOURCES:
            if not source.enabled:
                continue

            try:
                if source.key == "saramin":
                    jobs = self.crawl_saramin(source)
                elif source.key == "wanted":
                    jobs = self.crawl_wanted(source)
                elif source.key == "bokji":
                    # 복지넷은 일반 사회복지 공고도 많으므로 상담 관련 키워드 필터 활성화
                    jobs = self.crawl_generic_board(source, strict_keyword=True)
                else:
                    # 상담심리학회, 상담학회, 청소년상담복지개발원 전용 구인 게시판
                    jobs = self.crawl_generic_board(source, strict_keyword=False)

                all_new_jobs.extend(jobs)
            except Exception as e:
                logger.error("[%s] 크롤링 중 예외 발생: %s", source.name, e, exc_info=True)

        return all_new_jobs
