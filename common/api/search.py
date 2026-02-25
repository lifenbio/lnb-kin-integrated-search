"""
검색/테스트/디버깅 API
- 단일 키워드 테스트
- URL 매칭 디버깅
- 키워드 그룹 기반 수집
"""
import random
import time
from collections import namedtuple
from io import BytesIO
from openpyxl import Workbook
from ninja import Router
from bs4 import BeautifulSoup
from datetime import date
from django.conf import settings
from common.models import Keyword, URL, CrawlJob, CrawlResult
from common.package.naver_search import get_search_data
from common.package.mail import send
from common.crawler import process_keyword, get_excel_columns
from common.tasks import process_keyword_task, save_results_and_check_completion
from celery import chord, group
from common.utils import (
    get_normalized_url,
    is_post_url,
    load_ip_addresses,
)


search_router = Router()

# process_keyword()에 전달할 경량 키워드 객체 (DB 모델 불필요 시)
KeywordData = namedtuple('KeywordData', ['keyword', 'product_name'])


def _collect_and_send_excel(keyword_rows, filename, subject, to_emails):
    """키워드 목록을 수집하여 Excel 생성 + 이메일 발송하는 공통 함수

    Args:
        keyword_rows: (product_name, keyword) 또는 Keyword 모델 객체의 iterable
        filename: Excel 파일명
        subject: 이메일 제목
        to_emails: 수신자 이메일 리스트
    """
    ip_addresses = load_ip_addresses()
    bad_ip_addresses = []
    columns = get_excel_columns()
    result_data = []

    for row in keyword_rows:
        append_list = process_keyword(row, ip_addresses, bad_ip_addresses)
        result_data.append(append_list)
        time.sleep(random.uniform(2, 6))

    wb = Workbook()
    ws = wb.active
    ws.append(columns)
    for row in result_data:
        ws.append(row)
    excel_buffer = BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)

    file_list = [(filename, excel_buffer.getvalue(),
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]

    send(
        subject,
        "결과 파일 송부",
        to=to_emails,
        cc=[],
        files=file_list
    )

    return len(result_data)


# ============================================================
# 디버깅 API
# ============================================================

@search_router.get("/check-proxy", auth=None)
def check_proxy(request):
    """프록시 IP 상태 확인 - 처음 5개 IP로 테스트"""
    ip_addresses = load_ip_addresses()

    results = []
    for ip in ip_addresses[:5]:
        response, used_ip = get_search_data(
            "https://m.search.naver.com/search.naver?query=test",
            [ip]
        )
        results.append({"ip": ip, "status": response.status_code})

    ok_count = sum(1 for r in results if r["status"] == 200)
    return {
        "total_ips": len(ip_addresses),
        "tested": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": results
    }


@search_router.get("/debug-href", auth=None)
def debug_href(request, keyword: str):
    """검색 결과 아이템의 모든 href 값을 확인하는 진단용 API"""
    ip_addresses = load_ip_addresses()

    response, ip_address = get_search_data(
        f"https://m.search.naver.com/search.naver?sm=mtp_hty.top&where=m&query={keyword}",
        ip_addresses
    )

    if response.status_code != 200:
        return {"error": f"검색 실패: {response.status_code}"}

    soup = BeautifulSoup(response.text, 'html.parser')

    result = {"keyword": keyword, "주제판_아이템": [], "단일구좌_아이템": []}

    # 주제판 영역
    topic_sections = soup.select('div.fds-ugc-single-intention-item-list')
    for section in topic_sections:
        items = section.select('div[data-template-id="ugcItem"]')
        for idx, item in enumerate(items[:5]):
            hrefs = []
            for a_tag in item.select('a[href]'):
                hrefs.append(a_tag.get('href', ''))
            result["주제판_아이템"].append({"index": idx, "hrefs": hrefs})

    # 단일구좌 영역
    rra_sections = soup.select('div.fds-ugc-single-intention-item-list-rra')
    if not rra_sections:
        rra_sections = soup.find_all('div', class_=lambda x: x and 'fds-ugc-single-intention-item-list-rra' in x)
    for section in rra_sections:
        items = section.select('div[data-template-id="ugcItem"]')
        for idx, item in enumerate(items[:7]):
            hrefs = []
            for a_tag in item.select('a[href]'):
                hrefs.append(a_tag.get('href', ''))
            result["단일구좌_아이템"].append({"index": idx, "hrefs": hrefs})

    return result


@search_router.get("/debug-match", auth=None)
def debug_match(request, keyword: str):
    """URL 매칭 상세 디버깅 - 검색 결과와 DB URL 비교"""
    ip_addresses = load_ip_addresses()

    response, ip_address = get_search_data(
        f"https://m.search.naver.com/search.naver?sm=mtp_hty.top&where=m&query={keyword}",
        ip_addresses
    )

    if response.status_code != 200:
        return {"error": f"검색 실패: {response.status_code}"}

    soup = BeautifulSoup(response.text, 'html.parser')

    # DB URL 조회
    db_urls = []
    db_normalized_urls = []
    for row in URL.objects.filter(keyword=keyword):
        normalized = get_normalized_url(row.url)
        db_urls.append({
            "원본URL": row.url,
            "정규화URL": normalized
        })
        if normalized:
            db_normalized_urls.append(normalized)

    result = {
        "keyword": keyword,
        "DB_URL_개수": len(db_urls),
        "DB_URL_목록": db_urls,
        "DB_정규화URL_목록": db_normalized_urls,
        "검색결과_아이템": [],
        "매칭_결과": []
    }

    # 주제판 영역에서 URL 추출
    topic_sections = soup.select('div.fds-ugc-single-intention-item-list')
    for section in topic_sections:
        items = section.select('div[data-template-id="ugcItem"]')
        for item in items:
            _extract_debug_urls(item, db_normalized_urls, db_urls, result)

    # 단일구좌 영역에서 URL 추출
    rra_sections = soup.select('div.fds-ugc-single-intention-item-list-rra')
    for section in rra_sections:
        items = section.select('div[data-template-id="ugcItem"]')
        for item in items:
            _extract_debug_urls(item, db_normalized_urls, db_urls, result)

    result["검색결과_아이템_개수"] = len(result["검색결과_아이템"])
    result["매칭_개수"] = len(result["매칭_결과"])

    # 분석 결과
    if len(db_urls) == 0:
        result["분석"] = "DB에 이 키워드로 등록된 URL이 없습니다."
    elif len(result["검색결과_아이템"]) == 0:
        result["분석"] = "검색 결과에서 URL을 추출하지 못했습니다. HTML 구조를 확인하세요."
    elif len(result["매칭_결과"]) == 0:
        result["분석"] = "DB의 URL과 검색 결과의 URL이 일치하지 않습니다."
    else:
        result["분석"] = f"{len(result['매칭_결과'])}개의 URL이 매칭되었습니다."

    return result


def _extract_debug_urls(item, db_normalized_urls, db_urls, result):
    """디버깅용 URL 추출 헬퍼"""
    a_tags = item.select('a[href]')
    for a_tag in a_tags:
        href = a_tag.get('href', '')
        if any(x in href for x in ['blog.naver.com', 'cafe.naver.com', 'in.naver.com']) and is_post_url(href):
            normalized = get_normalized_url(href)
            is_match = normalized in db_normalized_urls if normalized else False

            result["검색결과_아이템"].append({
                "원본URL": href[:100] + "..." if len(href) > 100 else href,
                "정규화URL": normalized,
                "DB매칭": "O" if is_match else "X"
            })

            if is_match:
                result["매칭_결과"].append({
                    "검색결과_정규화URL": normalized,
                    "매칭된_DB_URL": [u for u in db_urls if u["정규화URL"] == normalized]
                })
            break


# ============================================================
# 테스트 API
# ============================================================

@search_router.get("/crawl-test", auth=None)
def crawl_test(request, keywords: str):
    """크롤링 로직 경량 테스트 (이메일/DB 불필요, JSON으로 즉시 결과 확인)

    사용법: /api/search/crawl-test?keywords=혈압정상수치,오메가3효능
    - process_keyword()를 직접 호출하여 실제 크롤링 로직을 테스트
    - Excel/이메일 발송 없이 API 응답으로 결과 반환
    """
    keyword_list = [k.strip() for k in keywords.split(',') if k.strip()]
    if not keyword_list:
        return {"error": "키워드를 입력해주세요. 예: ?keywords=혈압정상수치,오메가3효능"}

    ip_addresses = load_ip_addresses()
    bad_ip_addresses = []
    columns = get_excel_columns()
    results = []

    for kw in keyword_list:
        row = KeywordData(keyword=kw, product_name='테스트')
        data = process_keyword(row, ip_addresses, bad_ip_addresses)
        results.append(dict(zip(columns, data)))
        time.sleep(random.uniform(2, 6))

    return {
        "message": "크롤링 테스트 완료",
        "count": len(results),
        "columns": columns,
        "results": results,
        "bad_ips": bad_ip_addresses,
    }


@search_router.get("/run-test", auth=None)
def run_keyword_test(request, keywords: str):
    """키워드 목록으로 통합검색 수집 테스트 API (DB 불필요, 메일 발송)

    사용법: /api/search/run-test?keywords=혈압정상수치,혈압정상치,오메가3효능
    """
    keyword_list = [k.strip() for k in keywords.split(',') if k.strip()]
    if not keyword_list:
        return {"error": "키워드를 입력해주세요. 예: ?keywords=혈압정상수치,오메가3효능"}

    keyword_rows = [KeywordData(keyword=kw, product_name='테스트') for kw in keyword_list]
    count = _collect_and_send_excel(
        keyword_rows=keyword_rows,
        filename="테스트_통검결과.xlsx",
        subject="[카페통검] 테스트 결과 파일 송부",
        to_emails=["min4397@naver.com"]
    )
    return {"message": "테스트 통합검색 수집 완료", "keyword_count": count}


@search_router.get("/run-multi", auth=None)
def run_multi(request, limit: int = 100):
    """멀티서버 분산처리 수동 테스트 API

    사용법: 각 서버에서 /api/search/run-multi?limit=30 호출
    - 키워드 파티셔닝 (SERVER_ID 기반) + DB 결과 저장 + 완료 체크
    - 마지막 서버 완료 시 전체 Excel + 이메일 발송
    - beat 없이 수동으로 멀티서버 분산처리를 테스트할 때 사용
    """
    from common.utils import clear_bad_ips_in_redis

    server_id = settings.SERVER_ID
    total_servers = settings.TOTAL_SERVERS

    clear_bad_ips_in_redis()

    all_keyword_ids = list(
        Keyword.objects.all().order_by('id').values_list('id', flat=True)[:limit]
    )
    if not all_keyword_ids:
        return {"error": "DB에 키워드가 없습니다"}

    # CrawlJob 생성/조회
    today = date.today()
    job, created = CrawlJob.objects.get_or_create(
        job_date=today,
        defaults={
            'total_keywords': len(all_keyword_ids),
            'total_servers': total_servers,
        }
    )

    # 이 서버가 담당하는 키워드만 추출
    my_keyword_ids = [kid for i, kid in enumerate(all_keyword_ids) if i % total_servers == server_id]

    if not my_keyword_ids:
        return {"message": "이 서버에 할당된 키워드 없음", "server_id": server_id}

    tasks = [
        process_keyword_task.s(kid)
        for kid in my_keyword_ids
    ]

    chord(group(tasks))(save_results_and_check_completion.s())
    return {
        "message": "멀티서버 분산처리 디스패치 완료",
        "server_id": server_id,
        "total_servers": total_servers,
        "my_keywords": len(my_keyword_ids),
        "total_keywords": len(all_keyword_ids),
        "job_id": job.id,
        "job_created": created,
    }
