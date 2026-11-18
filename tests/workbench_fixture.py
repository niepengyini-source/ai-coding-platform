"""Create a fresh, fictional workbench for browser QA, only in ui-smoke.sqlite3."""
import csv
import hashlib
import io
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.ui_test_settings'
os.environ['PLATFORM_AI_BACKEND'] = 'disabled'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from coding.models import Code, Codebook, Membership, Project, SourceDocument, SourceRecord, Unit
from coding.services import create_round

assert Path(settings.DATABASES['default']['NAME']).name == 'ui-smoke.sqlite3'
with transaction.atomic():
    password = secrets.token_urlsafe(24)
    owner = get_user_model().objects.create_user('workbench_' + secrets.token_hex(4), password=password)
    project = Project.objects.create(name='课堂协作对话研究 · 界面测试', owner=owner, goal='纯虚构数据，仅验证工作台。')
    Membership.objects.create(project=project, user=owner, role='owner')
    book = Codebook.objects.create(project=project, version=1, policy='multi', title='协作过程编码')
    codes = [Code.objects.create(book=book, key=key, name=name, definition=definition,
        include_when='结合当前语句和前后文判断。', exclude_when='仅重复前文，不表达新的行为。')
        for key, name, definition in [
            ('PLAN', '计划与分工', '提出目标、步骤或成员分工。'),
            ('MON', '过程监控', '检查当前进度，指出待解决的问题。'),
            ('EVAL', '成果核对', '核验已有结果或评价完成质量。'),
            ('ACK', '回应与认可', '回应他人的提议或表达同意。'),
            ('ASK', '提出问题', 'Ask a question to clarify the task, evidence, or the next step. Check the surrounding conversation before making a decision.'),
            ('EXPLAIN', '说明理由', 'Explain a claim with an explicit reason or supporting evidence.'),
            ('COMPARE', '比较方案', 'Compare at least two possible approaches and describe their differences.'),
            ('PROPOSE', '提出方案', 'Suggest a concrete solution that the team can consider.'),
            ('CONNECT', '建立联系', 'Connect the current statement to an earlier idea or source.'),
            ('SUMMARIZE', '总结发现', 'Summarize what the group has established so far.'),
            ('VERIFY', '核对依据', 'Check whether a source actually supports the current conclusion.'),
            ('REVISE', '修订方案', 'Revise an earlier proposal using the available evidence.'),
        ]]
    texts = ['我们先分成两组，一组核对数据，另一组整理发现，最后一起确认。\n\n'
             '这次讨论的任务是为校园公共空间提出一份改善方案。请大家结合已经整理的访谈、观察记录和现场照片，比较不同使用者的需要。我们先把能够从材料中直接确认的情况列出来，再讨论可能的解释。\n\n'
             '第一组核对座椅、遮阴与通行条件，把对应的材料编号写在表格中；第二组整理同学们的建议，区分已有证据和暂时的猜测。遇到意见不一致的地方，先保留双方的理由，等查完原始记录后再作判断。\n\n'
             '最后请每组提出两项可以落实的改进，并说明依据、影响范围和仍需核实的问题。我们不急着选择一个答案，先确保大家讨论的是同一段材料。',
             '好，我负责核对第一部分的数据。', '第一部分还缺一条来源，我们先补上再继续。',
             '我把核对过的数字填回汇总表了，请大家再看一遍。']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['speaker', 'time', 'text', 'identity', 'message_id', 'room_id', 'seq_num', 'source_content_preview_snapshot'])
    rows = [(f'同学{position % 3 + 1}', f'10:{position % 60:02d}', texts[(position - 1) % 4]) for position in range(1, 57)]
    metadata = [{'speaker': speaker, 'time': time, 'identity': 'student',
                 'message_id': f'00000000-0000-4000-8000-{position:012}',
                 'room_id': '00000000-1111-4222-8333-000000000000', 'seq_num': position,
                 'source_content_preview_snapshot': '仅供界面验证的原始字段，不是编码答案。'}
                for position, (speaker, time, _) in enumerate(rows, 1)]
    for row, meta in zip(rows, metadata):
        writer.writerow([*row, meta['identity'], meta['message_id'], meta['room_id'], meta['seq_num'], meta['source_content_preview_snapshot']])
    raw = stream.getvalue().encode('utf-8-sig')
    doc = SourceDocument.objects.create(project=project, name='课堂对话.csv', raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(), text_column='text',
        display_mapping={'speaker': 'speaker', 'speaker_type': 'identity', 'time': 'time'})
    for position, (speaker, time, text) in enumerate(rows, 1):
        record = SourceRecord.objects.create(document=doc, position=position, text=text,
            metadata=metadata[position - 1], context_key='classroom')
        Unit.objects.create(record=record, text=text, end=len(text))
    round_ = create_round(project, owner, book.id, '个人编码 · 第一批', 'quick', [owner.id], 0, 2026, 'all', 0)
    tasks = list(round_.assignments.order_by('unit__record__position', 'id'))
    round_.assignments.filter(pk__in=[t.pk for t in tasks[:4]]).update(status='submitted', primary=codes[0], revision=1)
    credentials = {'username': owner.username, 'password': password, 'round': round_.pk,
        'project': project.pk, 'tasks': [t.pk for t in tasks], 'units': [t.unit_id for t in tasks], 'codes': [c.pk for c in codes]}
    (settings.RUNTIME_DIR / 'workbench-ui.json').write_text(json.dumps(credentials), encoding='utf-8')
print('Created fictional workbench in the isolated UI database. Credentials stay in runtime.')
