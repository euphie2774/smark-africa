import multiprocessing

bind = '0.0.0.0:8000'
worker_class = 'gevent'
workers = int(__import__('os').environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
timeout = 60
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
loglevel = __import__('os').environ.get('LOG_LEVEL', 'info').lower()
