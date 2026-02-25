FROM python:3.9-slim-bookworm

ENV PYTHONUNBUFFERED=1

# 필수 패키지 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    python3-dev \
    && apt-get clean

COPY requirements.txt /requirements.txt

# pip 최신화 및 requirements 설치
RUN pip install --upgrade pip && pip install -r /requirements.txt

WORKDIR /app

COPY . /app