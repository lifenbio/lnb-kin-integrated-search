from django.db import models
from django.contrib.auth.models import AbstractUser


class Account(AbstractUser):
    type = models.CharField(max_length=256, default='admin', verbose_name='유형')
    name = models.CharField(max_length=256, verbose_name='이름')
    phone_number = models.CharField(max_length=256, default='', verbose_name='핸드폰번호')

    class Meta:
        db_table = 'account'
        verbose_name = '사용자'
