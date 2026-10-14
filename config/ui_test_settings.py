"""Separate local-only database for browser QA; never uses the research database."""
from .settings import *
# Cookies are host-scoped, not port-scoped: do not disturb the real localhost login.
SESSION_COOKIE_NAME = 'ui_sessionid'
CSRF_COOKIE_NAME = 'ui_csrftoken'
STATIC_ROOT = RUNTIME_DIR / 'ui-staticfiles'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_DIR / 'ui-smoke.sqlite3',
                        'OPTIONS': {'timeout': 30, 'transaction_mode': 'IMMEDIATE'}}}
