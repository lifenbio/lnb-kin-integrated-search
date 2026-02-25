from django.db import models


class BaseModel(models.Model):
    created_dt = models.DateTimeField(auto_now_add=True, verbose_name='생성(등록)일시')
    
    class Meta:
        abstract = True


class Keyword(BaseModel):
    type = models.CharField(max_length=256, default='', verbose_name='구분')
    product_name = models.CharField(max_length=256, default='', verbose_name='제품명')
    keyword = models.CharField(max_length=256, default='', verbose_name='키워드', db_index=True)
    group = models.CharField(max_length=256, default='', verbose_name='그룹')
    priority = models.CharField(max_length=256, default='', verbose_name='우선순위')

    class Meta:
        db_table = 'keyword'
        verbose_name = '키워드'


class CrawlJob(models.Model):
    """하루 단위 크롤링 작업 상태 (서버 간 공유)"""
    job_date = models.DateField(unique=True)
    total_keywords = models.IntegerField()
    total_servers = models.IntegerField(default=1)
    report_sent = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'crawl_job'


class CrawlResult(models.Model):
    """키워드별 크롤링 결과 (서버가 처리 완료 후 저장)"""
    job = models.ForeignKey(CrawlJob, on_delete=models.CASCADE, related_name='results')
    keyword_id = models.IntegerField()
    server_id = models.IntegerField()
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'crawl_result'
        unique_together = [('job', 'keyword_id')]


class URL(BaseModel):
    product_name = models.CharField(max_length=256, default='', verbose_name='제품명')
    keyword = models.CharField(max_length=256, default='', verbose_name='키워드', db_index=True)
    part = models.CharField(max_length=256, default='', verbose_name='파트')
    url = models.CharField(max_length=1024, default='', verbose_name='url')
    conversion_keyword = models.CharField(max_length=256, default='', verbose_name='전환키워드')
    content_type = models.CharField(max_length=256, default='', verbose_name='원고형태')

    class Meta:
        db_table = 'url'
        verbose_name = 'URL'
