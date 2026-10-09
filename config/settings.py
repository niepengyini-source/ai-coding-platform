from pathlib import Path
import os
import secrets
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = BASE_DIR / 'runtime'
RUNTIME_DIR.mkdir(exist_ok=True)
# Minimal .env reader: existing process variables always take precedence.
env_file = BASE_DIR / '.env'
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

key_file = RUNTIME_DIR / 'secret.key'
if not os.environ.get('PLATFORM_SECRET_KEY') and not key_file.exists():
    try:
        with key_file.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
SECRET_KEY = os.environ.get('PLATFORM_SECRET_KEY') or key_file.read_text(encoding='utf-8')
DEBUG = os.environ.get('PLATFORM_DEBUG', '0') == '1'
PLATFORM_ALLOW_REGISTRATION = os.environ.get('PLATFORM_ALLOW_REGISTRATION', '1') == '1'
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('PLATFORM_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]
if '*' in ALLOWED_HOSTS:
    raise RuntimeError('请列出允许的主机地址，不使用通配符。')

INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
                  'django.contrib.messages', 'django.contrib.staticfiles', 'coding']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
              'coding.middleware.PasswordChangeMiddleware', 'coding.middleware.LoginThrottleMiddleware',
              'coding.middleware.AccountThrottleMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware',
              'coding.middleware.SecurityHeadersMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': ['django.template.context_processors.request',
                          'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages',
                          'coding.context_processors.account_options']}}]
WSGI_APPLICATION = 'config.wsgi.application'

db_url = os.environ.get('DATABASE_URL')
if db_url:
    url = urlparse(db_url)
    if url.scheme not in ('postgres', 'postgresql'):
        raise RuntimeError('DATABASE_URL目前仅支持PostgreSQL。')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(url.path.lstrip('/')),
                             'USER': unquote(url.username or ''), 'PASSWORD': unquote(url.password or ''),
                             'HOST': url.hostname or '127.0.0.1', 'PORT': url.port or 5432, 'CONN_MAX_AGE': 60}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_DIR / 'platform.sqlite3',
                             'OPTIONS': {'timeout': 30, 'transaction_mode': 'IMMEDIATE'},
                             'TEST': {'NAME': RUNTIME_DIR / 'test.sqlite3'}}}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
HTTPS = os.environ.get('PLATFORM_HTTPS', '0') == '1'
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = HTTPS
SECURE_SSL_REDIRECT = HTTPS
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 9 * 1024 * 1024
# Sessions remain in the database; this cache is only an extra single-process account throttle.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'platform-throttle'}}
