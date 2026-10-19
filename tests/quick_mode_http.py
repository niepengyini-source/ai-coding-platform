"""Real HTTP checks for quick coding, with the isolated fictional UI fixtures only."""
import json
import re
import runpy
import uuid
from html import unescape
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

fixture = runpy.run_path(str(Path(__file__).with_name('revision_http.py')))
prior_checks = len(fixture['checks'])
request, check = fixture['request'], fixture['check']
owner, project, round_ = fixture['owner'], fixture['project'], fixture['round_']
credentials, codes = fixture['credentials'], fixture['codes']
from coding.models import AuditEvent
from coding.workflow import coding_assignments

request('/logout/', {})
request('/login/')
request('/login/', {'username': credentials['username'], 'password': credentials['password']})
entries = list(coding_assignments(round_, owner))
current, following, later = next(entries[i:i + 3] for i in range(len(entries) - 2)
                                 if all(entry.revision == 0 for entry in entries[i:i + 3]))
path = f'/r/{round_.pk}/?a={current.pk}'
status, body, _ = request(path)
html = body.decode()
form = re.search(r'<form class="annotation-form"[^>]*>', html).group()
attrs = {name: unescape(value) for name, value in re.findall(r'(data-[\w-]+)="([^"]*)"', form)}
check('quick_form_scope_and_skip_target', status == 200 and attrs['data-shortcut-scope'] == str(round_.pk)
      and attrs['data-skip-url'] == f'/r/{round_.pk}/?a={following.pk}')
check('quick_nonmodal_mouse_access', 'aria-modal="false"' in html and 'data-shortcut-skip-prompt hidden' in html)
check('quick_optional_reason_controls', '跳过原因（选填）' in html and 'data-shortcut-skip-confirm' in html)
status, body, _ = request('/guide/')
check('quick_all_settings_present', status == 200 and body.count(b'data-shortcut-record=') == 11)


def save(entry, primary=None, note='', action='save'):
    token = next(c.value for c in fixture['cookies'] if c.name == 'ui_csrftoken')
    data = {'revision': entry.revision, 'book_id': round_.book_id, 'token': str(uuid.uuid4()),
            'action': action, 'primary': primary, 'secondary': [], 'uncertain': False,
            'no_code': False, 'note': note}
    message = Request(f'http://127.0.0.1:8001/api/assignments/{entry.pk}/save/',
                      data=json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json', 'X-CSRFToken': token})
    try:
        with fixture['opener'].open(message, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


# Saving an empty draft is explicit on skip, so it is available in personal history.
metadata = dict(current.unit.record.metadata)
status, result = save(current)
current.refresh_from_db()
check('quick_blank_skip_is_saved_draft', status == 200 and current.revision == 1 and current.status == 'draft'
      and current.primary_id is None and not current.no_code)
check('quick_blank_skip_retains_history', AuditEvent.objects.filter(
    project=project, action='annotation.save', object_id=str(current.pk)).exists())
status, body, _ = request(f'/p/{project.pk}/history/{current.pk}/')
check('quick_blank_draft_history_open', status == 200 and current.unit.text.encode() in body)
status, _ = save(current, note='虚构跳过原因：需要进一步核对上下文。')
current.refresh_from_db()
check('quick_skip_reason_retained_without_submission', status == 200 and current.status == 'draft'
      and current.note.startswith('虚构跳过原因'))
check('quick_skip_preserves_source_metadata', current.unit.record.metadata == metadata)

# Confirming B after skipping A must proceed to C, not immediately bounce back to A.
status, result = save(following, primary=codes[0].pk, action='submit')
following.refresh_from_db()
check('quick_primary_only_submit', status == 200 and following.status == 'submitted'
      and following.secondary == [])
check('quick_confirm_after_skip_continues_forward', result['next_url'] == f'/r/{round_.pk}/?a={later.pk}')
status, _ = save(current, action='submit')
current.refresh_from_db()
check('quick_empty_primary_cannot_submit', status == 400 and current.status == 'draft')
check('quick_other_coder_cannot_access', request(f'/api/assignments/{round_.assignments.filter(coder=fixture["coder"]).first().pk}/context/')[0] == 404)
print(json.dumps({'quick_mode_http_passed': len(fixture['checks']) - prior_checks,
                  'total_http_passed': len(fixture['checks'])}, ensure_ascii=False))
