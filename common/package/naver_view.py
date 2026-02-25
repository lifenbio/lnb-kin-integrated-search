import requests
import time
import hashlib
import hmac
import base64
from django.conf import settings


class Signature:
    @staticmethod
    def generate(timestamp, method, uri, secret_key):
        message = "{}.{}.{}".format(timestamp, method, uri)
        hash = hmac.new(bytes(secret_key, "utf-8"), bytes(message, "utf-8"), hashlib.sha256)

        hash.hexdigest()
        return base64.b64encode(hash.digest())


def get_header(method, uri, api_key, secret_key, customer_id):
    timestamp = str(round(time.time() * 1000))
    signature = Signature.generate(timestamp, method, uri, secret_key)

    return {'Content-Type': 'application/json; charset=UTF-8', 'X-Timestamp': timestamp,
            'X-API-KEY': api_key, 'X-Customer': str(customer_id), 'X-Signature': signature}


def get_view_data(query):
    BASE_URL = 'https://api.naver.com'
    API_KEY = settings.NAVER_API_KEY
    SECRET_KEY = settings.NAVER_SECRET_KEY
    CUSTOMER_ID = settings.NAVER_CUSTOMER_ID

    uri = '/keywordstool'
    method = 'GET'

    params = {
        'hintKeywords': query.replace(" ", ""),
        'showDetail': '1',
    }

    r = requests.get(BASE_URL + uri, params=params, headers=get_header(method, uri, API_KEY, SECRET_KEY, CUSTOMER_ID), timeout=15)

    if r.status_code == 200:
        keyword_list = r.json().get('keywordList', [])
        if not keyword_list:
            return 0, 0

        first = keyword_list[0]
        pc_view = first.get('monthlyPcQcCnt', 0)
        mobile_view = first.get('monthlyMobileQcCnt', 0)

        if isinstance(pc_view, str):
            pc_view = pc_view.replace('<', '').replace(' ', '')
        if isinstance(mobile_view, str):
            mobile_view = mobile_view.replace('<', '').replace(' ', '')

        return pc_view, mobile_view
    else:
        return 0, 0
