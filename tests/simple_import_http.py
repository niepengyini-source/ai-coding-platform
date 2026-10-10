"""Real local HTTP checks using only a separate, fictional UI-test database.

Run the server with config.ui_test_settings on 127.0.0.1:8001 first.
From the source root, run: python -m tests.simple_import_http
This script never accesses the research database or prints login credentials.
"""
import http.cookiejar
import json
import os
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.ui_test_settings'
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from coding.models import AuditEvent, Codebook, CodebookImportDraft, Membership, Project


if Path(settings.DATABASES['default']['NAME']).resolve() != (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve():
    raise SystemExit('Only the separate ui-smoke database is allowed.')

BASE = 'http://127.0.0.1:8001'
cookies = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(cookies))
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def request(path, data=None, file=None, csrf=True):
    body = None
    headers = {}
    if data is not None:
        data = dict(data)
        if csrf:
            data['csrfmiddlewaretoken'] = next(c.value for c in cookies if c.name == 'ui_csrftoken')
        if file is None:
            body = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            boundary = 'synthetic-' + uuid.uuid4().hex
            parts = []
            for key, value in data.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-rules.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode() + file + b'\r\n')
            parts.append(f'--{boundary}--\r\n'.encode())
            body = b''.join(parts)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    try:
        with opener.open(Request(BASE + path, data=body, headers=headers), timeout=15) as response:
            return response.status, response.read().decode('utf-8'), response.geturl()
    except HTTPError as error:
        return error.code, error.read().decode('utf-8'), error.geturl()


status, page, _ = request('/login/')
check('isolated_test_server', status == 200 and any(c.name == 'ui_csrftoken' for c in cookies)
      and not any(c.name == 'csrftoken' for c in cookies))

suffix = uuid.uuid4().hex
password = uuid.uuid4().hex + '-Synthetic!'
with transaction.atomic():
    user = get_user_model().objects.create_user('ui_import_' + suffix, password=password)
    project = Project.objects.create(name='简化导入 · 纯虚构HTTP检查', owner=user,
                                     goal='仅验证文件导入，不含真实研究材料。')
    Membership.objects.create(project=project, user=user, role='owner')
    book = Codebook.objects.create(project=project, version=1, title='纯虚构空草稿', policy='multi')

status, _, _ = request('/login/', {'username': user.username, 'password': password})
check('test_login', status == 200 and any(c.name == 'ui_sessionid' for c in cookies))
path = f'/p/{project.id}/books/{book.id}/import/'
status, page, _ = request(path)
check('simple_upload_page', status == 200 and '上传并查看结果' in page and 'name="map_definition"' not in page)
raw = (settings.BASE_DIR / 'examples' / 'codebook_simple.csv').read_bytes()
status, _, _ = request(path, {'action': 'upload', 'auto_preview': 'yes'}, file=raw, csrf=False)
check('real_csrf_protection', status == 403 and not CodebookImportDraft.objects.filter(book=book).exists())
status, page, url = request(path, {'action': 'upload', 'auto_preview': 'yes'}, file=raw)
check('upload_request_accepted', status == 200 and 'stage=preview' in url)
draft = CodebookImportDraft.objects.get(book=book)
check('automatic_preview', status == 200 and 'stage=preview' in url and '已自动整理，请核对' in page)
check('preview_does_not_import', draft.applied_book_id is None and book.codes.count() == 0)
check('correct_column_meanings', draft.preview['mapping']['definition'] == 'Definition of behavior'
      and draft.preview['mapping']['positive_example'] == 'Example'
      and draft.preview['codes'][0]['name'] == 'PLAN')
status, page, _ = request(path + '?draft=' + str(draft.id))
check('advanced_options_folded', status == 200 and 'id="book-import-advanced"' in page
      and 'id="book-import-advanced" open' not in page)
snapshot = draft.preview
draft.refresh_from_db()
check('adjust_get_keeps_saved_preview', draft.preview == snapshot)
confirmation = {'action': 'confirm', 'draft_id': str(draft.id)}
status, page, _ = request(path, confirmation)
check('explicit_confirmation_required', status == 200 and '请勾选' in page and book.codes.count() == 0)
confirmation['confirmed'] = 'yes'
status, page, _ = request(path, confirmation)
draft.refresh_from_db()
check('confirmed_rules_usable', status == 200 and draft.applied_book_id == book.id
      and book.codes.count() == 2 and book.codes.get(key='PLAN').name == 'PLAN')
status, _, _ = request(path, confirmation)
check('repeated_confirmation_is_safe', status == 200 and book.codes.count() == 2
      and AuditEvent.objects.filter(project=project, action='codebook.import').count() == 1)
request('/logout/', {})
print(json.dumps({'passed': len(checks), 'checks': checks,
                  'scope': 'local real HTTP; fictional isolated database; not browser or multi-device QA'},
                 ensure_ascii=False))
