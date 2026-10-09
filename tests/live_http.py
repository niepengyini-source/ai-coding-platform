"""HTTP transport smoke test on the synthetic local-only UI QA server.

Not a browser test; this verifies real Waitress, cookies, CSRF and API responses.
Run after seed_ui_test and server.py on port 8001 with config.ui_test_settings.
"""
import http.cookiejar
import json
import re
import uuid
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8001'
credentials = json.loads((ROOT / 'runtime' / 'ui-test-credentials.json').read_text(encoding='utf-8'))
cookies = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(cookies))
checks = []


def request(path, data=None, json_data=None, csrf=True):
    headers = {}
    if json_data is not None:
        data = json.dumps(json_data).encode()
        headers['Content-Type'] = 'application/json'
    elif data is not None:
        data = urlencode(data).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    if data is not None and csrf:
        headers['X-CSRFToken'] = next((c.value for c in cookies if c.name == 'ui_csrftoken'), '')
    req = Request(BASE + path, data=data, headers=headers)
    try:
        response = opener.open(req, timeout=15)
    except HTTPError as error:
        response = error
    return response.status, response.read(), response.headers


def check(name, condition):
    assert condition, name
    checks.append(name)


status, page, headers = request('/login/')
check('login_http', status == 200 and 'AI辅助编码平台'.encode() in page)
check('global_return_control_http', b'id="page-back"' in page)
navigation_path = re.search(rb'src="(/static/navigation\.[^"]+\.js)"', page).group(1).decode()
status, navigation, _ = request(navigation_path)
check('hashed_navigation_script_served', status == 200 and b'platform:before-back' in navigation)
css_path = re.search(rb'href="(/static/app\.[^"]+\.css)"', page).group(1).decode()
status, css, _ = request(css_path)
check('hashed_css_served', status == 200 and b'workbench-grid' in css)
js_path = re.search(rb'src="(/static/app\.[^"]+\.js)"', page).group(1).decode()
status, js, _ = request(js_path)
check('hashed_javascript_served', status == 200 and b'annotation-form' in js)
status, home, _ = request('/login/', data={'username': credentials['username'], 'password': credentials['password']})
check('cookie_login', status == 200 and '我的项目'.encode() in home)
r = credentials['active_round']
status, page, _ = request(f'/r/{r}/')
check('workbench_http', status == 200 and '我的编码'.encode() in page)
assignment_id = int(re.search(rb'data-endpoint="/api/assignments/(\d+)/save/"', page).group(1))
initial = json.loads(re.search(rb'<script id="annotation-data" type="application/json">(.*?)</script>', page, re.S).group(1))
primary = int(re.search(rb'<option value="(\d+)">', page).group(1))
payload = {'revision': initial['revision'], 'primary': primary, 'secondary': [], 'no_code': False,
           'uncertain': False, 'note': 'LIVE_HTTP_SYNTHETIC_NOTE', 'action': 'save', 'token': uuid.uuid4().hex}
status, _, _ = request(f'/api/assignments/{assignment_id}/save/', json_data=payload, csrf=False)
check('real_http_csrf_denied', status == 403)
status, body, _ = request(f'/api/assignments/{assignment_id}/save/', json_data=payload)
check('real_http_save', status == 200 and json.loads(body)['annotation']['note'] == payload['note'])
status, _, _ = request(f'/api/assignments/{assignment_id}/save/', json_data=payload)
check('idempotent_transport_retry', status == 200)
payload['token'] = uuid.uuid4().hex
status, _, _ = request(f'/api/assignments/{assignment_id}/save/', json_data=payload)
check('stale_http_save_conflict', status == 409)
status, _, _ = request(f'/r/{r}/review/')
check('blind_review_http_denied', status == 403)
status, _, _ = request(f'/r/{r}/export/all/')
check('blind_export_http_denied', status == 403)
status, _, _ = request(f'/r/{r}/disagreements/')
check('blind_queue_http_denied', status == 403)
status, _, _ = request(f'/r/{r}/export/disagreements/')
check('blind_disagreements_csv_http_denied', status == 403)
status, body, _ = request(f'/r/{r}/export/mine/')
check('own_csv_http', status == 200 and b'LIVE_HTTP_SYNTHETIC_NOTE' in body)
status, body, _ = request(f'/api/rounds/{r}/progress/')
check('progress_without_answers', status == 200 and set(json.loads(body)) == {'state', 'my_total', 'my_done', 'total', 'done'})
status, _, _ = request(f'/r/{credentials["review_round"]}/review/')
check('review_http_render', status == 200)
review_round = credentials['review_round']
status, page, _ = request(f'/r/{review_round}/disagreements/')
check('disagreement_queue_http_render', status == 200 and '主代码不同'.encode() in page)
unit_id = int(re.search(rb'href="/r/\d+/review/\?unit=(\d+)"', page).group(1))
status, page, _ = request(f'/r/{review_round}/review/?unit={unit_id}')
check('queued_unit_opens_exact_review', status == 200 and f'单元 #{unit_id}'.encode() in page)
status, body, _ = request(f'/r/{review_round}/export/disagreements/')
check('disagreements_csv_http', status == 200 and b'disagreement_types' in body and b'primary_difference' in body)
status, body, _ = request(f'/r/{review_round}/report/', data={})
check('report_snapshot_http', status == 200 and '主、次代码分别比较'.encode() in body and '一级维度'.encode() in body)
request('/logout/', data={})
status, body, _ = request(f'/r/{r}/')
# URL opener follows the anonymous redirect to the login page, but no coding text is served.
check('logout_removes_access', status == 200 and '登录工作空间'.encode() in body and b'LIVE_HTTP_SYNTHETIC_NOTE' not in body)
output = {'passed': len(checks), 'checks': checks, 'scope': 'real local HTTP against synthetic DB; not browser or multi-device QA'}
(ROOT / 'runtime' / 'http-test-result.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(output, ensure_ascii=False))
