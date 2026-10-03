# gunicorn.conf.py — Конфігурація Gunicorn для SchoolNet
bind = "0.0.0.0:8000"
workers = 3
worker_class = "gthread"
threads = 4
# Потоки дозволяють приймати великі файли без тайм-ауту sync-воркера.
timeout = 120
# Graceful timeout — скільки чекати воркера при перезапуску
graceful_timeout = 30
keepalive = 5
# Логування
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '[КЛІЄНТ IP: %(h)s] "%(r)s" %(s)s %(b)s %(M)sms'
