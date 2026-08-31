# Використовуємо офіційний легкий образ Python
FROM python:3.12-slim

# Встановлюємо системні залежності, необхідні для psycopg та деяких пакетів обробки файлів
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Налаштовуємо робочу директорію
WORKDIR /app

# Налаштування Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Копіюємо requirements-docker.txt та requirements.txt
COPY requirements.txt requirements-docker.txt /app/

# Встановлюємо залежності
# (pyqt6 ми залишаємо в оригінальному файлі, але для докера краще б його виключити,
# якщо він не компілюється. python:3.12-slim може не мати qt-залежностей. 
# Ми встановлюємо всі пакети)
RUN pip install --upgrade pip
RUN pip install -r requirements-docker.txt

# Копіюємо решту коду
COPY . /app/

# Створюємо директорії для медіа та статики
RUN mkdir -p /app/media /app/staticfiles /app/logs
RUN chmod -R 755 /app/media /app/staticfiles /app/logs

# Команда запуску через gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "schoolnet.wsgi:application"]
