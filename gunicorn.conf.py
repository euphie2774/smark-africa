import multiprocessing

bind = '0.0.0.0:8000'
# Use sync workers to avoid gevent/SSL recursion bug on Python 3.14
worker_class = 'sync'
workers = int(__import__('os').environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
threads = 4  # Use threads for concurrency instead of gevent
timeout = 120
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
loglevel = __import__('os').environ.get('LOG_LEVEL', 'info').lower()
