"""
공통 유틸리티 함수들 (모든 서버 동일, 수정 불필요)
- URL 정규화 및 비교
- IP 관리
- HTML 파싱 헬퍼
- Redis bad IP 관리
"""
import os
import re
import redis as redis_client
from common.models import URL


def get_normalized_url(url):
    """URL을 정규화하여 비교 가능한 형태로 변환"""
    if 'cafe.naver.com' in url:
        match = re.search(r'cafe\.naver\.com/([^/?]+/\d+)', url)
        if match:
            return match.group(1)
    elif 'blog.naver.com' in url:
        match = re.search(r'blog\.naver\.com/([^/?]+/\d+)', url)
        if match:
            return match.group(1)
    elif 'in.naver.com' in url:
        match = re.search(r'in\.naver\.com/([^/?]+/contents/internal/\d+)', url)
        if match:
            return match.group(1)
    return None


def is_post_url(url):
    """게시글 URL인지 확인 (게시글 ID가 포함되어 있는지)"""
    if 'blog.naver.com' in url:
        return bool(re.search(r'blog\.naver\.com/[^/?]+/\d+', url))
    elif 'cafe.naver.com' in url:
        return bool(re.search(r'cafe\.naver\.com/[^/?]+/\d+', url))
    elif 'in.naver.com' in url:
        return bool(re.search(r'in\.naver\.com/[^/?]+/contents/internal/\d+', url))
    return False


def compare_url(link_url, row_url):
    """두 URL을 비교하여 같은지 확인"""
    normalized_link_url = get_normalized_url(link_url)
    normalized_row_url = get_normalized_url(row_url)

    if normalized_row_url == normalized_link_url:
        return 'O'
    else:
        return 'X'


_url_cache = {}


def _get_keyword_urls(keyword):
    """키워드에 등록된 정규화 URL 목록을 캐시하여 반환 (키워드당 1회만 DB 조회)"""
    if keyword not in _url_cache:
        urls = set()
        for row in URL.objects.filter(keyword=keyword):
            normalized = get_normalized_url(row.url)
            if normalized:
                urls.add(normalized)
        _url_cache[keyword] = urls
    return _url_cache[keyword]


def check_url(keyword, link_url):
    """키워드에 등록된 URL과 링크 URL이 일치하는지 확인"""
    normalized_link = get_normalized_url(link_url)
    if not normalized_link:
        return 'X'
    return 'O' if normalized_link in _get_keyword_urls(keyword) else 'X'


def get_section_rank(soup, section_class):
    """섹션의 순위를 반환"""
    main_content = soup.select_one('main, div#content, div.main, #ct')

    if not main_content:
        main_content = soup

    sections = main_content.find_all('section')
    target_class = section_class.split()
    rank = -1

    for i, section in enumerate(sections):
        class_list = section.get('class', [])
        style = section.get('style', '')
        if 'display: none' in style or 'visibility: hidden' in style or 'hidden' in class_list:
            continue
        if all(cls in class_list for cls in target_class):
            rank = i + 1
            break

    if rank == -1:
        return 0
    else:
        return rank


_ip_cache = {}

def load_ip_addresses(file_path='/app/upload_data/ip_list.txt'):
    """IP 주소 목록을 파일에서 로드 (워커 프로세스 내 캐싱)"""
    if file_path not in _ip_cache:
        with open(file_path, 'r') as file:
            _ip_cache[file_path] = [line.strip() for line in file if line.strip()]
    return _ip_cache[file_path]


def get_valid_ip(ip_addresses, bad_ip_addresses=None):
    """유효한 IP 목록 반환 (실패한 IP 제외, Redis bad IP 반영)"""
    redis_bad = get_bad_ips_from_redis()
    local_bad = set(bad_ip_addresses) if bad_ip_addresses else set()
    all_bad = redis_bad | local_bad

    valid_ips = [ip for ip in ip_addresses if ip not in all_bad]
    if not valid_ips:
        clear_bad_ips_in_redis()
        if bad_ip_addresses is not None:
            bad_ip_addresses.clear()
        valid_ips = ip_addresses[:]
    return valid_ips


# ============================================================
# Redis 기반 IP 관리
# ============================================================

_redis_pool = None


def get_redis_connection():
    global _redis_pool
    if _redis_pool is None:
        from django.conf import settings
        _redis_pool = redis_client.ConnectionPool.from_url(settings.CELERY_BROKER_URL)
    return redis_client.Redis(connection_pool=_redis_pool)


def push_bad_ip_to_redis(ip, ttl=1800):
    """bad IP를 Redis에 30분간 기록"""
    r = get_redis_connection()
    r.sadd('bad_ip_addresses', ip)
    r.expire('bad_ip_addresses', ttl)


def get_bad_ips_from_redis():
    r = get_redis_connection()
    bad_ips = r.smembers('bad_ip_addresses')
    return {ip.decode() for ip in bad_ips} if bad_ips else set()


def clear_bad_ips_in_redis():
    r = get_redis_connection()
    r.delete('bad_ip_addresses')


def load_ip_pool():
    """전체 IP 풀 반환"""
    return load_ip_addresses()
