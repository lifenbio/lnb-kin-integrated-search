from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0002_delete_blockip'),
    ]

    operations = [
        migrations.CreateModel(
            name='CrawlJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('job_date', models.DateField(unique=True)),
                ('total_keywords', models.IntegerField()),
                ('total_servers', models.IntegerField(default=1)),
                ('report_sent', models.BooleanField(default=False)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'crawl_job',
            },
        ),
        migrations.CreateModel(
            name='CrawlResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('keyword_id', models.IntegerField()),
                ('server_id', models.IntegerField()),
                ('data', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='common.crawljob')),
            ],
            options={
                'db_table': 'crawl_result',
                'unique_together': {('job', 'keyword_id')},
            },
        ),
    ]
