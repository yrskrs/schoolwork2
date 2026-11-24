# Використовуємо офіційний легкий образ Python
FROM python:3.12-slim

# Встановлюємо системні залежності, необхідні для psycopg, обробки файлів та локального часового поясу
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    build-essential \
    libpq-dev \
    gcc \
    antiword \
    libreoffice-nogui \
    poppler-utils \
    mdbtools \
    fonts-dejavu-core \
    fonts-liberation \
    && ln -snf /usr/share/zoneinfo/Europe/Kyiv /etc/localtime \
    && echo "Europe/Kyiv" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Налаштовуємо робочу директорію
WORKDIR /app

# Налаштування Python та часового поясу
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Kyiv

# Копіюємо залежності локального запуску та фіксований серверний lock-файл
COPY requirements.txt requirements-docker.lock /app/

# Сервер використовує фіксовані залежності без необов’язкового Qt для ПК.
RUN pip install --no-cache-dir -r requirements-docker.lock && pip check

# Копіюємо решту коду
COPY . /app/

# Створюємо директорії для медіа та статики
RUN mkdir -p /app/media /app/staticfiles /app/logs
RUN chmod -R 755 /app/media /app/staticfiles /app/logs
ARG VCS_REF=local
ARG APP_VERSION=4.13.4
ARG SOURCE_DIGEST=unknown
RUN python -c 'import json,os,pathlib; pathlib.Path("/app/build-info.json").write_text(json.dumps({"revision":os.environ.get("VCS_REF","local"),"source_digest":os.environ.get("SOURCE_DIGEST","unknown")}))'

# Команда запуску через gunicorn з автоматичною міграцією БД та збором статики
CMD ["sh", "-c", "python manage.py migrate && python manage.py collectstatic --noinput && gunicorn --config /app/gunicorn.conf.py schoolnet.wsgi:application"]

LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.schoolnet.source-digest=$SOURCE_DIGEST \
      org.opencontainers.image.source="https://github.com/yrskrs/schoolwork2"
