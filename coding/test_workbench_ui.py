from django.test import TestCase, override_settings

from .models import Assignment, SourceRecord, Unit
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class WorkbenchInterfaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)

    def setUp(self):
        self.client.force_login(self.owner)

    def test_queue_is_scoped_to_current_coder_and_selected_task(self):
        own = self.r.assignments.get(coder=self.owner, unit=self.units[1])
        response = self.client.get(f'/r/{self.r.pk}/?a={own.pk}')
        self.assertContains(response, '/static/workbench/task-queue.js')
        self.assertContains(response, 'data-task-queue-fallback')
        for task in self.r.assignments.all():
            if task.coder_id == self.owner.pk:
                self.assertContains(response, f'data-task-id="{task.pk}"')
            else:
                self.assertNotContains(response, f'data-task-id="{task.pk}"')
        self.assertContains(response, f'href="?a={own.pk}&amp;page=1" aria-current="page"')
        self.assertContains(response, f'data-endpoint="/api/assignments/{own.pk}/save/"')

    def test_pagination_fallback_and_untrusted_material_text(self):
        for position in range(4, 57):
            record = SourceRecord.objects.create(document=self.doc, position=position, text='<img src=x onerror=alert(1)>')
            unit = Unit.objects.create(record=record, text=record.text, end=len(record.text))
            Assignment.objects.create(round=self.r, unit=unit, coder=self.owner)
        response = self.client.get(f'/r/{self.r.pk}/?page=2')
        self.assertContains(response, 'href="?page=1" data-task-previous')
        self.assertContains(response, '<span data-task-page>2 / 2</span>')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')

    def test_locked_controls_and_other_pages_keep_existing_behavior(self):
        own = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        own.status = 'submitted'
        own.save(update_fields=['status'])
        response = self.client.get(f'/r/{self.r.pk}/?a={own.pk}')
        self.assertContains(response, 'data-locked="true"')
        self.assertNotContains(response, 'data-action="submit_next"')
        self.assertContains(response, '已锁定，只读查看')
        for path in ['/login/', '/projects/', f'/p/{self.project.pk}/']:
            self.assertNotContains(self.client.get(path), '/static/workbench.css')
            self.assertNotContains(self.client.get(path), '/static/workbench/task-queue.js')

    def test_compact_header_preserves_tools_and_one_guarded_return(self):
        response = self.client.get(f'/r/{self.r.pk}/')
        self.assertContains(response, 'class="workbench-toolbar"', count=1)
        self.assertContains(response, 'id="page-back"', count=1)
        self.assertContains(response, 'data-icon-return="true"')
        self.assertNotContains(response, 'class="page-return"')
        self.assertNotContains(response, 'class="workbench-top"')
        self.assertContains(response, 'id="my-progress"', count=1)
        self.assertContains(response, 'id="round-progress"', count=1)
        for text in ['工作台工具', '显示设置', '任务管理', '成员进度', '导出我的编码', '我的编码历史']:
            self.assertContains(response, text)
        self.assertContains(response, 'class="workbench-menu account-menu"')
        self.assertContains(response, 'action="/logout/" method="post"')
        self.assertContains(response, '修改密码')
        self.assertContains(response, 'data-action="submit_next"')

    def test_project_toolbar_keeps_account_actions_and_guarded_return(self):
        response = self.client.get(f'/p/{self.project.pk}/')
        header = response.content.decode().split('<header class="topbar">')[1].split('</header>')[0]
        for text in ['class="workbench-menu account-menu"', '修改密码', 'action="/logout/" method="post"',
                     'id="page-back"', 'data-sidebar-toggle']:
            self.assertIn(text, header)
