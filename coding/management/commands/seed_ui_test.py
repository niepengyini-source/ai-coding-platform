import json
import secrets
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from coding.models import Project, Membership, Codebook, Code, SourceDocument, SourceRecord, Unit
from coding.services import create_round


class Command(BaseCommand):
    help = '仅在独立ui-smoke数据库准备纯虚构界面测试，不创建生产测试后门。'

    @transaction.atomic
    def handle(self, *args, **options):
        if Path(settings.DATABASES['default']['NAME']).name != 'ui-smoke.sqlite3':
            raise CommandError('仅允许在config.ui_test_settings下运行。')
        User = get_user_model()
        if User.objects.exists():
            self.stdout.write('界面测试数据库已有记录，未重复写入。')
            return
        password = secrets.token_urlsafe(18)
        owner = User.objects.create_user('ui_owner', password=password)
        coder = User.objects.create_user('ui_coder', password=secrets.token_urlsafe(18))
        p = Project.objects.create(name='团队访谈编码 · 虚构演示', goal='观察团队如何制定计划、检查进度与反思结果。',
            description='本项目是纯虚构界面测试，不含实际参与者材料。', owner=owner)
        for u, role in [(owner, 'owner'), (coder, 'coder')]:
            Membership.objects.create(project=p, user=u, role=role)
        b = Codebook.objects.create(project=p, version=1, title='团队行为初始规则', policy='multi')
        for key, name, definition, include, exclude in [
            ('PLAN', '计划与分工', '为后续活动确定目标、顺序或职责。', '明确提出之后要做的活动。', '仅叙述已完成的事情。'),
            ('CHECK', '检查与监控', '对照目标检查当前进度、时间或质量。', '评价当前进度并识别问题。', '只有计划，没有进度判断。'),
            ('REFLECT', '反思与调整', '根据过程或结果提出改进。', '说明原做法存在问题并调整。', '单纯重复任务要求。')]:
            Code.objects.create(book=b, key=key, name=name, definition=definition, include_when=include, exclude_when=exclude,
                                positive_example='我们发现时间不够，需要调整分工。', boundary_example='我们做完了。需看是否包含评价。')
        raw = (settings.BASE_DIR / 'examples' / 'sample.csv').read_bytes()
        import hashlib
        import csv
        import io
        doc = SourceDocument.objects.create(project=p, name='sample.csv', raw=raw, sha256=hashlib.sha256(raw).hexdigest(),
                                            text_column='text', context_column='interview_id')
        for i, row in enumerate(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))), 1):
            record = SourceRecord.objects.create(document=doc, position=i, text=row['text'],
                metadata={k: v for k, v in row.items() if k != 'text'}, context_key='value:' + row['interview_id'])
            Unit.objects.create(record=record, end=len(record.text), text=record.text)
        active = create_round(p, owner, b.id, '第一轮独立试编码', 'pilot', [owner.id, coder.id], 0, 2026, 'all', 25)
        review = create_round(p, owner, b.id, '分歧讨论演示', 'pilot', [owner.id, coder.id], 0, 2026, 'all', 25)
        plan = b.codes.get(key='PLAN'); check = b.codes.get(key='CHECK')
        for a in review.assignments.all():
            a.primary = plan if a.coder_id == owner.id else check
            a.note = '虚构测试：结合前后对话判断这个行为。'
            a.status, a.revision = 'submitted', 1
            a.save()
        review.state, review.revision = 'review', 1
        review.save()
        second = Project.objects.create(name='阅读体验开放题 · 虚构演示', goal='整理读者对材料表达和结构的体验。', owner=owner)
        Membership.objects.create(project=second, user=owner, role='owner')
        Codebook.objects.create(project=second, version=1, title='阅读体验分类')
        path = settings.RUNTIME_DIR / 'ui-test-credentials.json'
        path.write_text(json.dumps({'username': owner.username, 'password': password, 'active_round': active.id,
                                   'review_round': review.id, 'project': p.id}, ensure_ascii=False), encoding='utf-8')
        self.stdout.write('虚构界面项目已准备。测试登录信息仅在runtime中，不打印密码。')
