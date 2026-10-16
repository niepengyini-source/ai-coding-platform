"""Heading cleanup must not remove coding controls or version traceability."""
from django.core.cache import cache
from django.test import TestCase, override_settings

from .models import Assignment, Round
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class HeadingCleanupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)
        cls.assignment = Assignment.objects.get(
            round=cls.r, coder=cls.owner, unit=cls.units[0])
        cls.assignment.primary = cls.first
        cls.assignment.revision = 1
        cls.assignment.note = '合成测试记录'
        cls.assignment.save()

    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)

    def clean_page(self, path, heading):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        self.assertNotContains(response, 'class="eyebrow"')
        self.assertContains(response, f'<h1>{heading}')
        self.assertContains(response, '<aside class="sidebar">')
        self.assertContains(response, 'id="page-back"')
        return response

    def test_project_keeps_import_coding_and_version_list(self):
        response = self.clean_page(f'/p/{self.project.id}/', self.project.name)
        for text in ['导入材料', '上传并预览', '编码进度', '选择归档范围',
                     f'v{self.book.version} · {self.book.title}']:
            self.assertContains(response, text)
        self.assertNotContains(response, '>项目概览</div>')

    def test_workbench_keeps_coding_actions_and_round_status(self):
        response = self.clean_page(f'/r/{self.r.id}/', self.r.name)
        for text in ['我的编码', '确认并下一条', '保存草稿', '主代码',
                     self.r.get_state_display(), 'data-primary-fallback',
                     '我的编码历史', '导出我的编码']:
            self.assertContains(response, text)

    def test_codebook_keeps_frozen_status_codes_and_import_export(self):
        response = self.clean_page(
            f'/p/{self.project.id}/books/{self.book.id}/', self.book.title)
        for text in ['已冻结', self.first.key, self.first.definition,
                     '导入编码本', '导出编码本', '规则版本管理']:
            self.assertContains(response, text)

    def test_history_keeps_search_edit_and_record_version(self):
        response = self.clean_page(f'/p/{self.project.id}/history/', '我的编码历史')
        for text in ['精确条件', '搜索', '查看完整内容与修改历史',
                     '最后保存时间', f'v{self.book.version}', '修改 →']:
            self.assertContains(response, text)
        self.assertNotContains(response, '只显示你保存过的编码')

    def test_history_detail_keeps_record_and_modification_history(self):
        response = self.clean_page(
            f'/p/{self.project.id}/history/{self.assignment.id}/', '编码详情')
        for text in ['当前保存结果', '修改这条编码', '修改历史',
                     '保存版本 1', f'v{self.book.version}', self.assignment.note]:
            self.assertContains(response, text)

    def test_progress_keeps_distinct_counters_and_rule_version(self):
        response = self.clean_page(f'/p/{self.project.id}/progress/', '编码进度')
        for text in ['已提交', '已保存草稿', '尚未开始', '规则版本',
                     f'v{self.book.version}', '查看我的历史', 'data-progress-endpoint']:
            self.assertContains(response, text)
        self.assertNotContains(response, '已保存与已提交分开统计')

    def test_exports_keeps_download_controls_and_version_selector(self):
        response = self.clean_page(f'/p/{self.project.id}/exports/', '导出中心')
        for text in ['下一步：选择导出记录', '下载导出文件', '选择格式',
                     '自定义字段', f'v{self.book.version} · {self.book.title}']:
            self.assertContains(response, text)
        self.assertNotContains(response, '下载到你的电脑')

    def test_review_keeps_decision_and_discussion_controls(self):
        Assignment.objects.filter(round=self.r).update(status='submitted')
        Round.objects.filter(pk=self.r.id).update(state='review')
        response = self.clean_page(f'/r/{self.r.id}/review/', self.r.name)
        for text in ['查看分歧清单', '返回工作台', '最终协商结论',
                     '保存协商结论', '发表讨论', '已提交']:
            self.assertContains(response, text)

    def test_disagreements_keeps_filters_export_and_decision_status(self):
        Assignment.objects.filter(round=self.r).update(status='submitted')
        Round.objects.filter(pk=self.r.id).update(state='review')
        response = self.clean_page(
            f'/r/{self.r.id}/disagreements/?filter=all', '分歧与疑点清单')
        for text in ['返回工作台', '分歧或疑点', '尚未定稿', '全部单元',
                     '导出分歧CSV', '查看与协商']:
            self.assertContains(response, text)
