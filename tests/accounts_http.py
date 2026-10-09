"""Real HTTP account workflow; ONLY the synthetic QA server on localhost:8001.

Owner credentials and created fictional credentials stay inside ignored runtime.
No real account is read, created or changed by this test.
"""
import http.cookiejar
import json
import re
import secrets
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8001'
credentials = json.loads((ROOT / 'runtime' / 'ui-test-credentials.json').read_text(encoding='utf-8'))
checks = []


class Session:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def request(self, path, data=None, csrf=True):
        headers = {}
        if data is not None:
            data = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            if csrf:
                headers['X-CSRFToken'] = next((c.value for c in self.cookies if c.name == 'ui_csrftoken'), '')
        try:
            response = self.opener.open(Request(BASE + path, data=data, headers=headers), timeout=15)
        except HTTPError as exc:
            response = exc
        return response.status, response.read().decode('utf-8'), response.headers


def check(name, condition):
    assert condition, name
    checks.append(name)


owner, member = Session(), Session()
status, page, _ = member.request('/login/')
check('login_has_self_registration', status == 200 and '创建自己的账号' in page)
status, page, headers = member.request('/accounts/register/')
check('register_form_real_http', status == 200 and 'new-password' in page and headers.get('Cache-Control') == 'no-store')
username, password = 'qa_self_' + uuid.uuid4().hex[:10], secrets.token_urlsafe(18)
data = {'username': username, 'password1': password, 'password2': password, 'is_superuser': '1'}
status, _, _ = member.request('/accounts/register/', data, csrf=False)
check('register_csrf_required', status == 403)
status, page, _ = member.request('/accounts/register/', data)
check('self_registered_auto_login', status == 200 and '账号已创建' in page and '还没有加入项目' in page)
status, _, _ = member.request(f'/p/{credentials["project"]}/')
check('new_account_cannot_access_private_project', status == 404)
status, page, _ = member.request('/groups/join/')
check('join_form_real_http', status == 200 and '小组邀请码' in page)
status, page, _ = member.request('/groups/join/', {'invitation_code': 'A' * 20})
check('invalid_invitation_is_readable', status == 200 and '邀请码无效' in page)
owner.request('/login/')
status, _, _ = owner.request('/login/', {'username': credentials['username'], 'password': credentials['password']})
check('qa_owner_login', status == 200)
project = credentials['project']
status, page, _ = owner.request(f'/p/{project}/members/')
revision = re.search(r'name="revision" value="(\d+)"', page).group(1)
status, page, _ = owner.request(f'/p/{project}/invitation/', {'action': 'issue', 'revision': revision, 'days': 7})
code = re.search(r'id="new-invitation"[^>]*value="([A-Z2-9-]+)"', page).group(1)
check('invitation_generated_display_once', status == 200 and len(code.replace('-', '')) == 20)
status, page, _ = owner.request(f'/p/{project}/members/')
check('raw_code_removed_after_refresh', status == 200 and code not in page)
status, page, _ = member.request('/groups/join/', {'invitation_code': code.lower(), 'note': '纯虚构HTTP注册验收成员'})
check('join_becomes_pending_not_member', status == 200 and '等待审核' in page)
status, _, _ = member.request(f'/p/{project}/units/')
check('pending_user_cannot_see_materials', status == 404)
status, page, _ = owner.request(f'/p/{project}/members/')
application_block = re.search(r'<div class="join-review"><h3>' + username + r'</h3>(.*?)</form>', page, re.S).group(1)
review_path = re.search(r'action="([^"]+/review/)"', application_block).group(1)
application_revision = re.search(r'name="revision" value="(\d+)"', application_block).group(1)
check('owner_sees_real_join_request', '纯虚构HTTP注册验收成员' in application_block)
status, _, _ = member.request(review_path, {'revision': application_revision, 'action': 'approve', 'role': 'owner'})
check('applicant_cannot_approve_itself', status == 404)
status, page, _ = owner.request(review_path, {'revision': application_revision, 'action': 'approve', 'role': 'coder',
                                             'decision_note': '纯虚构HTTP验收：已核对'})
check('owner_approves_as_coder', status == 200 and '申请已通过' in page)
status, page, _ = member.request('/')
check('approved_project_appears_in_home', status == 200 and '已加入' in page and f'/p/{project}/' in page)
status, page, _ = member.request(f'/p/{project}/units/')
check('approved_user_can_read_synthetic_materials', status == 200 and '材料与编码单元' in page)
status, _, _ = member.request(f'/p/{project}/members/')
check('coder_cannot_manage_members', status == 403)
status, page, _ = owner.request(review_path, {'revision': application_revision, 'action': 'approve', 'role': 'reviewer'})
check('repeat_approval_is_rejected_readably', status == 200 and '已被处理或撤回' in page)
status, page, _ = member.request(f'/r/{credentials["active_round"]}/export/all/')
check('new_member_still_cannot_read_blind_answers', status == 403)
member.request('/logout/', {})
status, page, _ = member.request('/login/', {'username': username, 'password': password})
check('self_chosen_password_can_log_back_in', status == 200 and '我的项目' in page)
member.request('/logout/', {})
status, page, _ = member.request(f'/p/{project}/units/')
check('logout_removes_project_access', status == 200 and '登录工作空间' in page)

# Preserve a dedicated fictional account for browser QA without printing passwords.
(ROOT / 'runtime' / 'account-test-credentials.json').write_text(json.dumps({
    'username': username, 'password': password, 'project': project,
    'scope': 'fictional QA account in ui-smoke.sqlite3 only'}, ensure_ascii=False), encoding='utf-8')
output = {'passed': len(checks), 'checks': checks, 'scope': 'real local HTTP; synthetic QA database; not multi-device test'}
(ROOT / 'runtime' / 'account-http-result.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(output, ensure_ascii=False))
