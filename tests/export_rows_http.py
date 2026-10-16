"""Exercise real export forms using the separate, fictional UI test fixture."""
import http.cookiejar
import json
import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.ui_test_settings'
import django
django.setup()
from django.conf import settings

assert Path(settings.DATABASES['default']['NAME']).resolve() == (settings.RUNTIME_DIR / 'ui-smoke.sqlite3').resolve()
fixture = json.loads((settings.RUNTIME_DIR / 'ui-revision-credentials.json').read_text(encoding='utf-8'))
cookies = http.cookiejar.CookieJar()
client = build_opener(HTTPCookieProcessor(cookies))


def request(path, values=None):
    if values is not None:
        values = dict(values)
        values['csrfmiddlewaretoken'] = next(cookie.value for cookie in cookies if cookie.name == 'ui_csrftoken')
    body = urlencode(values, doseq=True).encode() if values is not None else None
    with client.open(Request('http://127.0.0.1:8001' + path, data=body), timeout=10) as response:
        return response.read(), response.headers


class ExportForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.values = {}
        self.checks = []
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.active = attrs.get('id') == 'export-options'
        if not self.active:
            return
        name = attrs.get('name')
        if tag == 'input' and name and 'disabled' not in attrs:
            if attrs.get('type') == 'checkbox':
                if name == 'selected':
                    self.checks.append(attrs['value'])
                if 'checked' not in attrs:
                    return
                self.values.setdefault(name, []).append(attrs.get('value', 'on'))
            else:
                self.values[name] = attrs.get('value', '')
        if tag == 'select':
            self.select = name
        if tag == 'option' and self.select:
            if self.select not in self.values or 'selected' in attrs:
                self.values[self.select] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False
        if tag == 'select':
            self.select = None


request('/login/')
request('/login/', {'username': fixture['username'], 'password': fixture['password']})
url = f'/p/{fixture["project"]}/exports/'
html, _ = request(url + '?' + urlencode({'target': 'mine', 'round': fixture['round']}))
first = ExportForm(html.decode())
assert len(first.checks) == 25, 'Prepare at least 26 fictional owner assignments before running this check.'
first_id = first.checks[3]
html, headers = request(url, dict(first.values, row_page='2', selected=[first_id], selection='selected'))
assert 'Content-Disposition' not in headers
second = ExportForm(html.decode())
assert second.values['retained_selected'] == first_id
second_id = second.checks[0]
values = dict(second.values, selected=[second_id], selection='selected', format='json', action='download')
body, headers = request(url, values)
payload = json.loads(body)
assert 'attachment;' in headers['Content-Disposition']
assert payload['row_count'] == 2
assert {str(row['assignment_id']) for row in payload['rows']} == {first_id, second_id}
assert all(json.loads(row['metadata_json'])['group_id'] == row['meta_group_id'] for row in payload['rows'])
assert all(json.loads(row['metadata_json'])['created_at'] == row['meta_created_at'] for row in payload['rows'])
body, _ = request(url, dict(first.values, selection='first', limit='7', format='json', action='download'))
assert json.loads(body)['row_count'] == 7
body, _ = request(url, dict(first.values, row_query='NO_FICTIONAL_MATCH', action='filter'))
empty = ExportForm(body.decode())
assert empty.values['visible_ids'] == ''
assert not empty.checks
for extension in ('xlsx', 'csv', 'tsv'):
    body, headers = request(url, dict(values, format=extension))
    assert headers['Content-Disposition'].endswith('.' + extension)
request('/logout/', {})
print('PASS: real forms, cross-page selection, exact two rows, first N, empty search, original metadata, JSON/XLSX/CSV/TSV.')
