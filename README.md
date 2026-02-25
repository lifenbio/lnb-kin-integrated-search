## 네이버 통합검색 크롤링 프로젝트

네이버 모바일 통합검색 결과를 크롤링하여 Excel 리포트로 자동 생성/발송하는 시스템.
Celery chord 패턴으로 분산처리하며, 팀별 서버에서 `common/crawler.py`만 교체하여 운영.

## 기술 스택
- Django 4.1.2 + Django Ninja (API)
- Celery + Redis (분산 태스크 처리)
- PostgreSQL (DB)
- Docker Compose (배포)
- AWS EC2 + RDS + S3 (프로덕션)

## 프로젝트 구조

```
common/
  crawler.py           ← 팀별 교체 파일 (크롤링 로직 + Excel 컬럼 + 이메일 설정)
  tasks.py             ← 분산 인프라 (Celery chord, 태스크, 집계) - 공통
  utils.py             ← 공통 유틸 (IP 관리, URL 비교, Redis) - 공통
  models.py            ← Keyword, URL 모델
  api/
    search.py          ← 테스트/디버깅 API
  package/
    naver_search.py    ← HTTP 요청 (X-Forwarded-For 헤더 IP)
    naver_view.py      ← 네이버 검색량 조회 API
    mail.py            ← 이메일 발송
main/
  celery.py            ← Celery 설정 + beat 스케줄 (매일 00:00)
  settings/
    base.py, dev.py, prod.py
```

## 분산처리 흐름
```
celery_beat (매일 00:00)
  → celery_orchestrator: integration_area_collection (chord 디스패치)
    → celery_worker: process_keyword_task × N개 병렬 처리
      → celery_orchestrator: aggregate_and_send_report (Excel 생성 + 이메일 발송)
```

## 사용방법

### 로컬 개발환경

```
도커 설치
https://hub.docker.com/editions/community/docker-ce-desktop-mac
(M1이면 apple chip, 아니면 intel chip)

컴포즈 실행 및 migration, create superuser
docker-compose up -d --build
docker-compose exec app python manage.py makemigrations
docker-compose exec app python manage.py migrate
docker-compose exec app python manage.py createsuperuser
```

### 배포 환경

도커 설치 (추후 하기 작성 명령어 스크립트 처리 예정)

```
오래된 도커 버전이 있을시 삭제
sudo apt-get remove docker docker-engine docker.io

설치에 필요한 패키지 설치
sudo apt-get update && sudo apt-get install \
    apt-transport-https \
    ca-certificates \
    curl \
    software-properties-common

패키지 저장소 추가
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   $(lsb_release -cs) \
   stable"

아래 명령어를 통해 docker 패키지 검색 되는지 확인
sudo apt-get update && sudo apt-cache search docker-ce

도커 CE 설치
sudo apt-get update && sudo apt-get install docker-ce

도커 컴포즈 설치
sudo apt-get install docker-compose
```

컴포즈 실행 및 마이그레이션, collectstatic, create superuser

```
docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml exec web python manage.py makemigrations
docker-compose -f docker-compose.prod.yml exec web python manage.py migrate
docker-compose -f docker-compose.prod.yml exec web python manage.py collectstatic
docker-compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

## 테스트 API

| 엔드포인트 | 설명 |
|------------|------|
| `/api/search/check-proxy` | 프록시 IP 상태 확인 (5개 테스트) |
| `/api/search/run-distributed?limit=N` | Celery chord 분산처리 테스트 |
| `/api/search/run-group` | 순차 처리 테스트 |
| `/api/search/run-test?keywords=키워드1,키워드2` | 임의 키워드 테스트 |
| `/api/search/test?keyword=키워드` | 단일 키워드 파싱 결과 확인 |

## Docker Compose 서비스

| 서비스 | 역할 |
|--------|------|
| `app` | Django API 서버 (Uvicorn) |
| `db` | PostgreSQL |
| `redis` | Celery broker + result backend |
| `celery_orchestrator` | chord 디스패치 + 결과 집계 워커 |
| `celery_worker_1` | 키워드 처리 워커 (concurrency=5) |
| `celery_beat` | 스케줄러 (매일 00:00 실행) |
| `flower` | Celery 모니터링 (localhost:5555) |

## 팀별 서버 배포 (카페 → 지식인 등)
1. 프로젝트 전체 복사
2. `common/crawler.py` 교체 (팀 고유 크롤링 로직/Excel 컬럼/이메일 설정)
3. `common/models.py` 조정 (팀별 모델 필드)
4. `.env` 파일 설정 (DB, Redis, API 키)
5. `docker-compose up` — 분산처리 자동 동작

## 설정방법

`settings` 폴더에는 `base.py`, `dev.py`, `prod.py` 이렇게 세 가지의 파일이 있습니다.
`base.py` 는 기본 설정 파일입니다.
`dev.py` 에는 로컬 환경 혹은 개발 시 환경에 대한 설정을 추가하시면 됩니다.
`prod.py` 에는 실제 서비스 환경에서의 설정을 추가하시면 됩니다.

## License
MIT
