# 네이버 통합검색 크롤링 프로젝트

## 아키텍처
- Django 4.1.2 + Celery + Redis + PostgreSQL
- 분산처리: **멀티서버 키워드 파티셔닝** + Celery chord 패턴
- 결과 합산: 공유 RDS(PostgreSQL)에 결과 저장 → 마지막 서버가 Excel 생성 + 이메일 발송
- 배포: Docker Compose, AWS EC2 + RDS

## 팀별 서버 운영 구조
각 팀(카페, 지식인 등)이 별도 서버/DB에서 운영.
프로젝트 코드는 동일하되, `common/crawler.py`만 팀별로 교체.

## 파일 역할

### 팀별 교체 파일
- `common/crawler.py` — **팀별 교체 파일** (크롤링 로직 + Excel 컬럼 + 이메일 설정)

### 공통 인프라 (모든 서버 동일, 수정 불필요)
- `common/tasks.py` — 분산 인프라 (Celery chord, 키워드 파티셔닝, DB 결과 저장, 완료 체크, 리포트 합산)
- `common/models.py` — DB 모델 (Keyword, URL, CrawlJob, CrawlResult)
- `common/utils.py` — 공통 유틸 (IP 관리, URL 비교, Redis bad IP, HTML 파싱)
- `common/package/naver_search.py` — HTTP 요청 (X-Forwarded-For 헤더로 IP 전달)
- `common/package/naver_view.py` — 네이버 검색량 조회 API
- `common/package/mail.py` — 이메일 발송

### 배포 파일
- `docker-compose.yml` — **dev용** (로컬 PostgreSQL 컨테이너 포함)
- `docker-compose.prod.yml` — **prod용** (DB 없음, 외부 RDS 연결)
- `.env.dev` — dev 환경변수 (로컬 DB)
- `.env.prod` — prod 환경변수 (RDS + SERVER_ID)

### API
- `common/api/search.py` — 테스트/디버깅 API 엔드포인트

---

## 멀티서버 분산처리

### 개요
3385개 키워드를 한 서버에서 처리하면 503 차단이 빈번하므로, N대 서버가 키워드를 나눠 처리한다.
각 서버는 자기 파티션의 키워드만 크롤링하고, 결과를 공유 RDS에 저장한다.
마지막으로 끝나는 서버가 전체 결과를 합산하여 하나의 Excel로 이메일 발송한다.

### 환경변수
| 변수 | 설명 | 예시 |
|------|------|------|
| `TOTAL_SERVERS` | 전체 서버 수 | `4` |
| `SERVER_ID` | 이 서버의 인덱스 (0부터 시작) | `0`, `1`, `2`, `3` |

`TOTAL_SERVERS=1`이면 단일 서버로 기존과 동일하게 동작한다.

### 키워드 파티셔닝 원리
```
전체 키워드:  [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]    (TOTAL_SERVERS=4)

Server 0 (idx%4==0):  1,  5,  9
Server 1 (idx%4==1):  2,  6, 10
Server 2 (idx%4==2):  3,  7, 11
Server 3 (idx%4==3):  4,  8, 12
```
나머지(modulo) 연산이라 **겹치지 않고, 빠지는 것도 없다**.
4대 서버가 같은 RDS에서 같은 키워드를 `ORDER BY id`로 읽으므로 순서도 동일.

### 인프라 구조
```
Server 0 (SERVER_ID=0)         Server 1 (SERVER_ID=1)         ...  Server 3
┌─────────────────────┐       ┌─────────────────────┐       ┌──────────────┐
│ local Redis (broker) │       │ local Redis (broker) │       │ local Redis  │
│ beat → orchestrator  │       │ beat → orchestrator  │       │ beat → orch  │
│ 3 workers            │       │ 3 workers            │       │ 3 workers    │
│ keywords idx%4==0    │       │ keywords idx%4==1    │       │ idx%4==3     │
└────────┬────────────┘       └────────┬────────────┘       └──────┬───────┘
         └──────────────┬──────────────┘────────────────────────────┘
                        ▼
                 공유 RDS (PostgreSQL)
                 ┌───────────────────┐
                 │ Keyword, URL      │
                 │ CrawlJob          │  ← 작업 상태 추적
                 │ CrawlResult       │  ← 키워드별 결과 저장
                 └───────────────────┘
```

- 각 서버는 **자체 Redis**를 Celery broker로 사용 (서버 간 Redis 공유하지 않음)
- **RDS만 공유** — 키워드 읽기, 결과 저장, 완료 체크 모두 RDS를 통해 동기화

### 실행 흐름 (매일 00:00 자동 실행)

```
celery_beat (00:00) — 4대 서버 각각 동시 트리거
  │
  ▼
integration_area_collection()
  ① 전체 키워드 ID 조회 (3385개)
  ② CrawlJob 생성 (get_or_create → 첫 서버만 INSERT)
  ③ 키워드 파티셔닝: idx % TOTAL_SERVERS == SERVER_ID인 것만 추출
  ④ chord(group(process_keyword_task × N))(save_results_and_check_completion)
  │
  ▼
process_keyword_task() × N개 병렬 처리
  - 네이버 모바일 검색 → HTML 파싱 → 결과 반환
  - 실패 시 무한 재시도 (지수 백오프: 60s, 120s, 240s, 300s...)
  │
  ▼
save_results_and_check_completion()  — 각 서버의 chord 콜백
  ① 결과를 crawl_result 테이블에 bulk_create
  ② 완료 체크: SELECT COUNT(*) vs total_keywords
  ③ 미완료 → "SERVER_N_DONE" 반환 후 종료
  ④ 전부 완료 → UPDATE crawl_job SET report_sent=True (atomic)
     → rows_updated=1인 서버만 리포트 생성 권한 획득
  ⑤ _generate_full_report() → 전체 Excel 생성 + 이메일 발송
```

### DB 모델

**CrawlJob** — 하루 단위 크롤링 작업 상태 (서버 간 공유)
| 필드 | 타입 | 설명 |
|------|------|------|
| job_date | DateField (unique) | 작업 날짜 |
| total_keywords | IntegerField | 전체 키워드 수 |
| total_servers | IntegerField | 투입 서버 수 |
| report_sent | BooleanField | 리포트 발송 완료 여부 |
| started_at | DateTimeField | 시작 시각 |
| completed_at | DateTimeField | 완료 시각 |

**CrawlResult** — 키워드별 크롤링 결과
| 필드 | 타입 | 설명 |
|------|------|------|
| job | FK → CrawlJob | 소속 작업 |
| keyword_id | IntegerField | 키워드 ID |
| server_id | IntegerField | 처리한 서버 ID |
| data | JSONField | 결과 리스트 (Python list 그대로) |
| created_at | DateTimeField | 저장 시각 |

- `unique_together = (job, keyword_id)` → 같은 키워드 중복 저장 방지

### 레이스 컨디션 방지
```python
CrawlJob.objects.filter(id=job.id, report_sent=False).update(report_sent=True)
```
- PostgreSQL row-level locking으로 딱 한 서버만 `rows_updated=1` 반환
- 나머지 서버는 `rows_updated=0` → 리포트 생성 스킵

---

## AWS 인프라 구성

### RDS 생성 (1회)

| 항목 | 설정 |
|------|------|
| 엔진 | PostgreSQL 14 |
| 인스턴스 | db.t3.micro (프리티어) 또는 db.t3.small |
| 스토리지 | 20GB gp3 |
| 리전 | ap-northeast-2 (서울) |
| VPC | EC2와 동일한 VPC |
| 퍼블릭 액세스 | 아니오 |
| DB 이름 | `ksol` |
| 마스터 사용자 | `postgres` |
| 보안그룹 인바운드 | PostgreSQL(5432) ← EC2 보안그룹 |

### EC2 (크롤링 서버 4대)

| 항목 | 설정 |
|------|------|
| 인스턴스 | t3.small (2 vCPU / 2GB) |
| AMI | Amazon Linux 2023 |
| 리전 | ap-northeast-2 (서울) |
| VPC | RDS와 동일한 VPC |
| 보안그룹 인바운드 | SSH(22), HTTP(8000) |
| 보안그룹 아웃바운드 | 전체 허용 |

### 보안그룹 요약
```
[EC2 보안그룹]
  인바운드: 22(SSH), 8000(API)
  아웃바운드: 전체

[RDS 보안그룹]
  인바운드: 5432 ← EC2 보안그룹 (소스를 EC2 SG ID로 지정)
  아웃바운드: 전체
```

### 월 예상 비용
```
EC2 t3.small × 4대:  ~$76/월
RDS db.t3.micro:     ~$15/월 (프리티어 1년 무료)
────────────────────────────
총:                  ~$91/월
```

---

## 배포 절차 (4대 서버)

### Step 1. RDS 생성
```
AWS 콘솔 → RDS → 데이터베이스 생성
  엔진: PostgreSQL 14
  템플릿: 프리티어 (db.t3.micro) 또는 프로덕션 (db.t3.small)
  DB 인스턴스 식별자: cafe-crawl-db
  마스터 사용자 이름: postgres
  마스터 암호: <설정>
  초기 데이터베이스 이름: ksol
  VPC: EC2와 동일한 VPC
  퍼블릭 액세스: 아니오
  보안그룹: EC2에서 5432 인바운드 허용
```
생성 완료 후 엔드포인트 확인 (예: `cafe-crawl-db.xxxx.ap-northeast-2.rds.amazonaws.com`)

### Step 2. EC2 4대 생성
```
AWS 콘솔 → EC2 → 인스턴스 시작
  AMI: Amazon Linux 2023
  인스턴스 유형: t3.small
  키 페어: 기존 또는 새로 생성
  VPC: RDS와 동일
  보안그룹: SSH(22) + 8000 인바운드
  수량: 4대
```

### Step 3. 각 EC2에 Docker 설치
```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# docker-compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 재접속 (docker 그룹 반영)
exit
```

### Step 4. 프로젝트 배포 + .env.prod 설정
```bash
git clone <레포지토리> ~/app && cd ~/app
```

각 서버의 `.env.prod` — **SERVER_ID만 다르고 나머지는 동일**:
```env
# Server 0 (.env.prod)
DEBUG=0
SECRET_KEY=<시크릿키>
DJANGO_ALLOWED_HOSTS=*
SQL_ENGINE=django.db.backends.postgresql
SQL_DATABASE=ksol
SQL_USER=postgres
SQL_PASSWORD=<RDS 비밀번호>
SQL_HOST=cafe-crawl-db.xxxx.ap-northeast-2.rds.amazonaws.com
SQL_PORT=5432

TOTAL_SERVERS=4
SERVER_ID=0          # ← 서버마다 0, 1, 2, 3

# ... 나머지 API키, 이메일 등 동일
```

### Step 5. DB 마이그레이션 (Server 0에서 1회만)
```bash
cd ~/app
docker-compose -f docker-compose.prod.yml run --rm app python manage.py migrate
```

### Step 6. 4대 실행
```bash
# 각 서버에서
cd ~/app
docker-compose -f docker-compose.prod.yml up -d --build
```

### Step 7. 검증
```bash
# 각 서버에서 수동 테스트 (beat 없이)
curl http://localhost:8000/api/search/run-multi?limit=20

# 응답 확인
# Server 0: {"server_id": 0, "my_keywords": 5, "total_keywords": 20, ...}
# Server 1: {"server_id": 1, "my_keywords": 5, "total_keywords": 20, ...}
```

DB 확인:
```sql
SELECT * FROM crawl_job WHERE job_date = CURRENT_DATE;
SELECT server_id, COUNT(*) FROM crawl_result GROUP BY server_id;
```

---

## Docker Compose 파일 구분

| 파일 | 용도 | DB | 실행 방법 |
|------|------|-----|----------|
| `docker-compose.yml` | dev (로컬) | 로컬 PostgreSQL 컨테이너 | `docker-compose up` |
| `docker-compose.prod.yml` | prod (EC2) | 외부 RDS 연결 | `docker-compose -f docker-compose.prod.yml up` |

---

## 새 팀 서버 세팅 순서
1. 프로젝트 전체 복사
2. `common/crawler.py` 교체 (팀 고유 크롤링/컬럼/이메일)
3. `common/models.py` 조정 (팀별 Keyword/URL 모델 필드)
4. `.env.prod` 설정 (RDS, Redis, API 키 등)
5. `docker-compose -f docker-compose.prod.yml up` — 분산처리 자동 동작

---

## crawler.py 인터페이스 규약
다른 팀의 crawler.py는 반드시 다음을 export해야 함:
- `TEAM_CONFIG` dict: `filename`, `email_subject`, `email_to` 키 필수
- `get_excel_columns()` → list
- `process_keyword(row, ip_addresses, bad_ip_addresses)` → list
  - row: Keyword 모델 객체 (row.keyword, row.product_name 필수)
  - 반환 리스트 길이 = get_excel_columns() 길이와 동일해야 함
  - **주의**: 반환값에 numpy 타입(int64 등) 포함 금지 — Celery JSON 직렬화 실패함. `get_view_data()` 등에서 받은 값은 `.item()`으로 Python 네이티브 타입 변환 필수

## 테스트 API 엔드포인트
- `/api/search/check-proxy` — 프록시 IP 상태 확인 (5개 테스트)
- `/api/search/debug-href?keyword=키워드` — 검색 결과 href 확인
- `/api/search/debug-match?keyword=키워드` — 검색 결과 vs DB URL 매칭 비교
- `/api/search/crawl-test?keywords=키워드1,키워드2` — process_keyword() 경량 테스트 (이메일/DB 불필요)
- `/api/search/run-test?keywords=키워드1,키워드2` — Excel + 이메일 발송 파이프라인 테스트 (DB 불필요)
- `/api/search/run-multi?limit=N` — **멀티서버 분산처리 수동 테스트** (파티셔닝 + DB 저장 + 완료 체크 + 리포트 발송)

## 검증 순서
1. dev에서 `TOTAL_SERVERS=1` 테스트 (기존과 동일 동작 확인)
2. `/api/search/run-multi?limit=20`으로 멀티서버 분산처리 확인
3. DB에서 `crawl_job`, `crawl_result` 테이블 데이터 확인
4. 이메일 수신 시 전체 키워드 포함 여부 확인

## 주의사항
- `naver_search.py`의 IP 방식은 **X-Forwarded-For 헤더** 방식임. 프록시 방식(`proxies=`)으로 변경하면 전체 503 에러 발생
- `naver_view.py`의 `get_view_data()`는 pandas DataFrame에서 값을 꺼내므로 numpy int64 반환. Celery 태스크에서 사용 시 반드시 Python int로 변환
- celery_beat 스케줄이 전체 키워드를 디스패치하므로 dev 환경 테스트 시 beat 비활성화 권장
- `CrawlJob`은 `job_date` unique → 하루에 한 작업만 존재. 같은 날 재실행하면 기존 job을 조회함
- `CrawlResult`는 `(job, keyword_id)` unique → 같은 키워드 중복 저장 불가 (ignore_conflicts 처리)
- prod에서는 반드시 `docker-compose -f docker-compose.prod.yml` 사용 (로컬 DB 컨테이너 없음)
