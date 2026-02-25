"""
지식인팀 크롤러 - 팀별 교체 파일
다른 팀 서버에서는 이 파일만 교체하면 분산처리 인프라 그대로 사용 가능

[모델 필드 추가 필요]
Keyword 모델: priority = CharField(max_length=256, default='')
URL 모델: conversion_keyword = CharField(max_length=256, default='')
URL 모델: content_type = CharField(max_length=256, default='')
"""
import re
import json
import random
import sys
import time
from datetime import datetime
from bs4 import BeautifulSoup

from common.package.naver_search import get_search_data
from common.package.naver_view import get_view_data
from common.utils import get_valid_ip
from common.models import URL


def _log(msg):
    print(msg, flush=True, file=sys.stderr)


# ============================================================
# 인터페이스 export (tasks.py가 import)
# ============================================================

TEAM_CONFIG = {
    'filename': '1그룹_지식인통검.xlsx',
    'email_subject': '[지식인통검(1그룹)] 결과 파일 송부',
    'email_to': ['min4397@naver.com'],
}


def get_excel_columns():
    """지식인팀 Excel 컬럼 정의 (21개)"""
    return [
        '수집월', '수집일', '키워드제품명', '매칭키워드', '검색어',
        '우선순위', '검색량(P)', '검색량(M)', '모통지식인블럭유무',
        '모통노출유무', '모통노출위치', 'ID', '송출유무', '질문일자',
        '종류', 'URL제품', '전환키워드', '원고형태', '발행키워드',
        '송출URL', '누적조회수',
    ]


# ============================================================
# 지식인 전용 내부 함수
# ============================================================

def _get_normalized_kin_url(url):
    """지식인 URL에서 docId를 추출하여 정규화"""
    match = re.search(r'docId=(\d+)', url)
    if match:
        return match.group(1)
    return None


def _check_kin_url(keyword, link_url):
    """키워드에 등록된 URL과 링크 URL이 일치하는 URL 객체 반환

    카페팀 check_url()과 다르게 URL 객체를 반환한다.
    (product_name, conversion_keyword, content_type 등 접근 필요)

    Returns:
        매칭된 URL 객체 또는 None
    """
    normalized_link = _get_normalized_kin_url(link_url)
    if not normalized_link:
        return None
    for row in URL.objects.filter(keyword=keyword):
        normalized_row = _get_normalized_kin_url(row.url)
        if normalized_row and normalized_link == normalized_row:
            return row
    return None


def _get_kin_section_rank(soup):
    """지식인 섹션의 순위를 반환 (fender-root 블록 기준)"""
    fender_roots = soup.find_all('div', attrs={'data-fender-root': 'true'})
    rank = 0
    for root in fender_roots:
        rank += 1
        if root.get('data-meta-ssuid') == 'kin':
            return rank
    return 0


def _extract_kin_items(soup):
    """지식인 섹션에서 모든 kinItem 추출 (여러 kin 블록 지원)

    Returns:
        list[dict]: url, author_id, badge 키를 가진 딕셔너리 리스트
    """
    kin_sections = soup.select('div[data-meta-ssuid="kin"]')
    if not kin_sections:
        return []

    items = []
    for kin_section in kin_sections:
        kin_item_elements = kin_section.select('div[data-template-id="kinItem"]')
        for kin_item in kin_item_elements:
            item_data = {'url': '', 'author_id': '', 'badge': ''}

            button = kin_item.select_one('button._keep_trigger')
            if button:
                item_data['url'] = button.get('data-url', '')

            author_span = kin_item.select_one('.sds-comps-profile-info-title-text span.sds-comps-text-type-body1')
            if author_span:
                item_data['author_id'] = author_span.get_text(strip=True)

            badge_span = kin_item.select_one('span.sds-comps-text-type-badge')
            if badge_span:
                item_data['badge'] = badge_span.get_text(strip=True)

            items.append(item_data)

    return items


def _extract_kin_detail(response_text):
    """상세 페이지에서 질문일자, 조회수 추출

    Returns:
        (question_date, view_count) 튜플
    """
    soup = BeautifulSoup(response_text, 'html.parser')
    question_date = ''
    view_count = ''

    user_info = soup.select_one('div.userInfo')
    if user_info:
        for item in user_info.select('span.infoItem'):
            text = item.get_text(strip=True)
            if '조회수' in text:
                view_count = text.replace('조회수', '').strip()
            elif '작성일' in text:
                date_match = re.search(r'\d{4}\.\d{2}\.\d{2}', text)
                if date_match:
                    question_date = date_match.group(0)

    if not view_count:
        for span in soup.find_all('span'):
            text = span.get_text(strip=True)
            if '조회수' in text:
                view_count = text.replace('조회수', '').strip()
                break

    if not question_date:
        for span in soup.find_all('span'):
            text = span.get_text(strip=True)
            if '작성일' in text:
                date_match = re.search(r'\d{4}\.\d{2}\.\d{2}', text)
                if date_match:
                    question_date = date_match.group(0)
                    break

    return question_date, view_count


# ============================================================
# 메인 크롤링 함수
# ============================================================

def process_keyword(row, ip_addresses, bad_ip_addresses):
    """
    지식인팀: 단일 키워드 크롤링 + 파싱

    Args:
        row: Keyword 모델 객체 (row.keyword, row.product_name, row.priority 필수)
        ip_addresses: 사용 가능한 IP 목록
        bad_ip_addresses: 실패한 IP 목록 (mutable)

    Returns:
        append_list: 결과 데이터 리스트 (길이 = get_excel_columns() 길이 = 21)
    """
    MAX_RETRIES = 20
    _log(f"[크롤링 시작] keyword={row.keyword}")

    now = datetime.now()
    collect_month = now.strftime('%Y-%m')
    collect_date = now.strftime('%Y.%m.%d')

    # 1. 자동완성 키워드 조회
    for _retry in range(MAX_RETRIES):
        valid_ips = get_valid_ip(ip_addresses, bad_ip_addresses)
        response, ip_address = get_search_data(
            f"https://mac.search.naver.com/mobile/ac?_callback=_jsonp_0&q={row.keyword}&con=1&q_enc=UTF-8&st=1&frm=mobile_nv&r_format=json&r_enc=UTF-8&r_unicode=0&t_koreng=1&ans=2&run=2&rev=4",
            valid_ips
        )
        if response.status_code == 200:
            time.sleep(random.uniform(1, 3))
            break
        bad_ip_addresses.append(ip_address)
        backoff = min(10 + 3 * _retry, 25)
        time.sleep(random.uniform(backoff, backoff + 5))
    else:
        raise RuntimeError(f"자동완성 조회 실패: {MAX_RETRIES}회 재시도 초과 (keyword={row.keyword})")

    # 자동완성 키워드 추출
    json_string = response.text[response.text.index('(') + 1 : response.text.rindex(')')]
    json_string = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', json_string)
    auto_keywords = json.loads(json_string)

    auto_keyword = row.keyword
    for auto_words in auto_keywords['items'][0]:
        cleaned_auto_word = auto_words[0].replace(" ", "")
        if cleaned_auto_word.lower() == row.keyword.lower():
            auto_keyword = auto_words[0]

    # 2. 검색량 조회
    pc_view, mobile_view = get_view_data(auto_keyword)
    # numpy int64 → Python int 변환 (Celery JSON 직렬화 호환)
    if hasattr(pc_view, 'item'):
        pc_view = pc_view.item()
    if hasattr(mobile_view, 'item'):
        mobile_view = mobile_view.item()

    # 3. 네이버 모바일 검색
    for _retry in range(MAX_RETRIES):
        valid_ips = get_valid_ip(ip_addresses, bad_ip_addresses)
        response, ip_address = get_search_data(
            f"https://m.search.naver.com/search.naver?sm=mtp_hty.top&where=m&query={auto_keyword}",
            valid_ips
        )
        if response.status_code == 200:
            time.sleep(random.uniform(1, 3))
            break
        bad_ip_addresses.append(ip_address)
        backoff = min(10 + 3 * _retry, 25)
        time.sleep(random.uniform(backoff, backoff + 5))
    else:
        raise RuntimeError(f"모바일 검색 실패: {MAX_RETRIES}회 재시도 초과 (keyword={row.keyword})")

    soup = BeautifulSoup(response.text, 'html.parser')

    # 4. 지식인 섹션 탐지
    kin_sections = soup.select('div[data-meta-ssuid="kin"]')
    kin_block_exists = 1 if kin_sections else 0

    # 5. 지식인 섹션 순위
    kin_rank = _get_kin_section_rank(soup) if kin_block_exists else 0

    # 6. 모든 kinItem 추출 및 URL DB 매칭
    kin_items = _extract_kin_items(soup)

    exposure_yn = 0
    author_id = ''
    send_yn = 0
    question_date = ''
    badge = ''
    url_product = ''
    conv_keyword = ''
    content_type = ''
    publish_keyword = ''
    send_url = ''
    view_count = ''

    matched_item = None
    matched_url_obj = None
    for item in kin_items:
        if item['url']:
            matched = _check_kin_url(row.keyword, item['url'])
            if matched:
                matched_item = item
                matched_url_obj = matched
                break

    if matched_item and matched_url_obj:
        exposure_yn = 1
        author_id = matched_item['author_id']
        send_yn = 1
        badge = matched_item['badge']
        url_product = matched_url_obj.product_name
        conv_keyword = matched_url_obj.conversion_keyword
        content_type = matched_url_obj.content_type
        publish_keyword = auto_keyword
        send_url = matched_url_obj.url

        # 상세 페이지 크롤링 (질문일자 + 조회수)
        for _retry in range(MAX_RETRIES):
            valid_ips = get_valid_ip(ip_addresses, bad_ip_addresses)
            detail_response, ip_address = get_search_data(
                matched_item['url'],
                valid_ips
            )
            if detail_response.status_code == 200:
                time.sleep(random.uniform(1, 3))
                break
            bad_ip_addresses.append(ip_address)
            backoff = min(10 + 3 * _retry, 25)
            time.sleep(random.uniform(backoff, backoff + 5))
        else:
            raise RuntimeError(f"상세페이지 조회 실패: {MAX_RETRIES}회 재시도 초과 (keyword={row.keyword})")

        question_date, view_count = _extract_kin_detail(detail_response.text)

    # 21개 컬럼 순서대로 리스트 생성
    append_list = [
        collect_month,      # 수집월
        collect_date,       # 수집일
        row.product_name,   # 키워드제품명
        row.keyword,        # 매칭키워드
        auto_keyword,       # 검색어
        getattr(row, 'priority', ''),  # 우선순위
        pc_view,            # 검색량(P)
        mobile_view,        # 검색량(M)
        kin_block_exists,   # 모통지식인블럭유무
        exposure_yn,        # 모통노출유무
        kin_rank,           # 모통노출위치
        author_id,          # ID
        send_yn,            # 송출유무
        question_date,      # 질문일자
        badge,              # 종류
        url_product,        # URL제품
        conv_keyword,       # 전환키워드
        content_type,       # 원고형태
        publish_keyword,    # 발행키워드
        send_url,           # 송출URL
        view_count,         # 누적조회수
    ]

    expected_len = len(get_excel_columns())
    _log(f"[크롤링 완료] keyword={row.keyword}, 컬럼={len(append_list)}/{expected_len}")
    if len(append_list) != expected_len:
        _log(f"[컬럼 불일치!] keyword={row.keyword}, {len(append_list)} != {expected_len}")

    return append_list
