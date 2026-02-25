from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0003_crawljob_crawlresult'),
    ]

    operations = [
        migrations.AlterField(
            model_name='keyword',
            name='keyword',
            field=models.CharField(db_index=True, default='', max_length=256, verbose_name='키워드'),
        ),
        migrations.AlterField(
            model_name='url',
            name='keyword',
            field=models.CharField(db_index=True, default='', max_length=256, verbose_name='키워드'),
        ),
    ]
