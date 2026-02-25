"""
Celery 태스크 - 분산처리 인프라
크롤링 로직은 common/crawler.py에 분리 (팀별 교체 대상)
"""
import time
from io import BytesIO
from openpyxl import Workbook
from datetime import date, datetime
from django.conf import settings
from common.models import Keyword, CrawlJob, CrawlResult
from common.package.mail import send
from celery import shared_task, chord, group
from celery.exceptions import MaxRetriesExceededError
from common.utils import (
    load_ip_pool,
    push_bad_ip_to_redis,
    clear_bad_ips_in_redis,
    get_redis_connection,
)
from common.crawler import process_keyword, get_excel_columns, TEAM_CONFIG

REDIS_JOB_START_KEY = 'chord_job_started_at'


@shared_task(bind=True, max_retries=10, acks_late=True, time_limit=1200)
def process_keyword_task(self, keyword_id, **kwargs):
    """개별 키워드 처리 Celery 태스크"""
    try:
        keyword = Keyword.objects.get(id=keyword_id)
        ip_addresses = load_ip_pool()
        bad_ip_addresses = []

        result = process_keyword(keyword, ip_addresses, bad_ip_addresses)

        for ip in bad_ip_addresses:
            push_bad_ip_to_redis(ip)

        return {'keyword_id': keyword_id, 'data': result, 'status': 'success'}
    except Exception as exc:
        try:
            # crawler.py 내부 재시도(20회)가 전부 실패한 경우만 여기 도달
            # 짧은 백오프로 재시도 (3, 6, 10, 10, 10...)
            countdown = min(3 * (2 ** self.request.retries), 10)
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            import logging
            logging.getLogger(__name__).error(
                f"keyword_id={keyword_id} 최종 실패 (max_retries 초과): {exc}"
            )
            return {'keyword_id': keyword_id, 'data': [], 'status': 'failed'}


@shared_task(time_limit=600)
def save_results_and_check_completion(results):
    """chord 콜백 - 결과 DB 저장 + 모든 서버 완료 체크 + 리포트 발송"""
    server_id = settings.SERVER_ID
    today = date.today()

    job = CrawlJob.objects.get(job_date=today)

    # 결과 DB 저장 (bulk_create + ignore_conflicts로 중복 방지)
    # 실패 키워드도 빈 data로 저장하여 완료 카운트에 포함
    valid = [r for r in results if r and r.get('keyword_id')]
    crawl_results = [
        CrawlResult(
            job=job,
            keyword_id=r['keyword_id'],
            server_id=server_id,
            data=r['data'],
        )
        for r in valid
    ]
    CrawlResult.objects.bulk_create(crawl_results, ignore_conflicts=True)

    # 완료 체크
    completed_count = CrawlResult.objects.filter(job=job).count()
    if completed_count < job.total_keywords:
        return {
            "message": f"SERVER_{server_id}_DONE",
            "completed": completed_count,
            "total": job.total_keywords,
        }

    # 모든 키워드 완료 → 리포트 발송 시도 (atomic: 한 서버만 성공)
    rows_updated = CrawlJob.objects.filter(
        id=job.id, report_sent=False
    ).update(report_sent=True, completed_at=datetime.now())

    if not rows_updated:
        return {"message": "REPORT_ALREADY_SENT"}

    # Excel 생성 + 이메일 발송
    job.refresh_from_db()
    return _generate_full_report(job)


def _generate_full_report(job):
    """전체 결과 합산 보고서 생성 + 이메일 발송"""
    columns = get_excel_columns()
    all_results = CrawlResult.objects.filter(job=job).order_by('keyword_id')
    data_rows = [r.data for r in all_results]

    wb = Workbook()
    ws = wb.active
    ws.append(columns)
    for row in data_rows:
        ws.append(row)
    excel_buffer = BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)

    # 소요시간 계산
    elapsed_seconds = (job.completed_at - job.started_at).total_seconds()
    hours, remainder = divmod(int(elapsed_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    elapsed_str = f"{hours}시간 {minutes}분 {seconds}초"

    body = (
        f"결과 파일 송부\n"
        f"성공: {len(data_rows)}건\n"
        f"총 소요시간: {elapsed_str}\n"
        f"서버: {job.total_servers}대"
    )

    file_list = [(TEAM_CONFIG['filename'], excel_buffer.getvalue(),
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]

    try:
        send(
            TEAM_CONFIG['email_subject'],
            body,
            to=TEAM_CONFIG['email_to'],
            cc=[],
            files=file_list,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"이메일 발송 실패, report_sent 롤백: {e}")
        CrawlJob.objects.filter(id=job.id).update(report_sent=False)
        return {
            "message": "EMAIL_FAILED",
            "error": str(e),
            "success": len(data_rows),
        }

    return {
        "message": "REPORT_SENT",
        "success": len(data_rows),
        "elapsed": elapsed_str,
    }


@shared_task
def integration_area_collection():
    """오케스트레이터 - 키워드 파티셔닝 + chord 패턴 분산처리 디스패치"""
    server_id = settings.SERVER_ID
    total_servers = settings.TOTAL_SERVERS

    # 중복 실행 방지: Redis 락 (같은 서버에서 같은 날 재실행 차단)
    r = get_redis_connection()
    lock_key = f'orchestrator_lock:{server_id}:{date.today().isoformat()}'
    if not r.set(lock_key, '1', nx=True, ex=79200):
        return {"message": "ALREADY_RUNNING", "server_id": server_id}

    clear_bad_ips_in_redis()

    # 시작 시간 Redis에 기록
    r.set(REDIS_JOB_START_KEY, str(time.time()), ex=86400)

    all_keyword_ids = list(Keyword.objects.all().order_by('id').values_list('id', flat=True))
    if not all_keyword_ids:
        return {"message": "NO_KEYWORDS"}

    # CrawlJob 생성/조회 (첫 서버만 생성, 나머지는 조회)
    today = date.today()
    CrawlJob.objects.get_or_create(
        job_date=today,
        defaults={
            'total_keywords': len(all_keyword_ids),
            'total_servers': total_servers,
        }
    )

    # 이 서버가 담당하는 키워드만 추출
    my_keyword_ids = [kid for i, kid in enumerate(all_keyword_ids) if i % total_servers == server_id]

    if not my_keyword_ids:
        return {"message": "NO_KEYWORDS_FOR_SERVER", "server_id": server_id}

    tasks = [
        process_keyword_task.s(kid)
        for kid in my_keyword_ids
    ]

    chord(group(tasks))(save_results_and_check_completion.s())
    return {
        "message": "DISPATCHED",
        "server_id": server_id,
        "my_keywords": len(my_keyword_ids),
        "total_keywords": len(all_keyword_ids),
    }
