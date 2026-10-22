"""Submitted-task lookup uses fictional records and preserves independent answers."""
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import Assignment, AuditEvent, Membership, Round, SourceRecord, Unit
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class SubmissionNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)
        cls.r.assignments.filter(coder=cls.coder).update(status='submitted', primary=cls.second,
            note='PEER_PRIVATE_NOTE_DO_NOT_SHOW', revision=2)
        for position in range(4, 62):
            text = '虚构分页材料' + str(position)
            record = SourceRecord.objects.create(document=cls.doc, position=position, text=text)
            unit = Unit.objects.create(record=record, text=text, end=len(text))
            Assignment.objects.create(round=cls.r, unit=unit, coder=cls.coder, status='submitted',
                primary=cls.second, note='PEER_PRIVATE_NOTE_DO_NOT_SHOW', revision=2)
        cls.current = cls.r.assignments.filter(coder=cls.owner).first()
        cls.last = cls.r.assignments.filter(coder=cls.coder).order_by('unit__record__position').last()

    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)
        self.url = f'/r/{self.r.pk}/'

    def page(self, **params):
        return self.client.get(self.url, params)

    def panel(self, response):
        return response.content.decode().split('id="submission-controls"', 1)[1].split('</details>', 1)[0]

    def state(self):
        return {
            'round': list(Round.objects.values('id', 'state', 'revision', 'cycle')),
            'answers': list(Assignment.objects.order_by('pk').values(
                'id', 'revision', 'status', 'primary_id', 'secondary', 'note', 'superseded')),
            'audit': AuditEvent.objects.count(),
        }

    def test_first_and_second_pages_cover_all_submitted_tasks(self):
        first = self.page(a=self.current.pk)
        self.assertEqual(first.context['submitted_tasks'].paginator.count, 61)
        self.assertEqual(len(first.context['submitted_tasks']), 50)
        second = self.page(a=self.current.pk, submitted_page=2)
        self.assertEqual(len(second.context['submitted_tasks']), 11)
        ids = {row['id'] for row in first.context['submitted_tasks']} | {
            row['id'] for row in second.context['submitted_tasks']}
        self.assertEqual(len(ids), 61)
        self.assertIn(self.last.pk, ids)
        self.assertContains(second, f'/assignments/{self.last.pk}/reopen/')
        self.assertContains(second, 'aria-label="已提交任务分页"')

    def test_lookup_reaches_task_beyond_first_fifty_by_system_unit_id(self):
        response = self.page(a=self.current.pk, submitted_unit=self.last.unit_id)
        self.assertEqual([row['id'] for row in response.context['submitted_tasks']], [self.last.pk])
        self.assertTrue(response.context['submission_filters_active'])
        self.assertContains(response, '系统单元编号')
        self.assertContains(response, '不是材料顺序号')
        self.assertNotContains(response, 'PEER_PRIVATE_NOTE_DO_NOT_SHOW')
        self.assertNotIn(self.last.unit.text, self.panel(response))

    def test_member_filter_and_unit_filter_work_together(self):
        self.current.status, self.current.primary, self.current.revision = 'submitted', self.first, 2
        self.current.save(update_fields=['status', 'primary', 'revision'])
        response = self.page(a=self.current.pk, submitted_unit=self.current.unit_id,
                             submitted_coder=self.coder.pk)
        self.assertEqual(response.context['submitted_tasks'].paginator.count, 1)
        self.assertEqual(response.context['submitted_tasks'][0]['coder__username'], self.coder.username)
        self.assertNotIn('primary', response.context['submitted_tasks'][0])
        self.assertNotIn('note', response.context['submitted_tasks'][0])
        self.assertContains(response, f'value="{self.coder.pk}" selected')

    def test_empty_result_keeps_search_and_reset_visible(self):
        response = self.page(a=self.current.pk, submitted_unit=999999)
        self.assertEqual(response.context['submitted_tasks'].paginator.count, 0)
        self.assertContains(response, '查找已提交任务')
        self.assertContains(response, '清除筛选')
        self.assertContains(response, '没有匹配的已提交任务')

    def test_invalid_unit_values_get_friendly_empty_results(self):
        for value in ('0', '-1', '1.2', '１２', 'abc', '9' * 30):
            with self.subTest(value=value):
                response = self.page(a=self.current.pk, submitted_unit=value)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['submitted_tasks'].paginator.count, 0)
                self.assertIn('系统单元编号', response.context['submission_filter_error'])

    def test_foreign_or_invalid_member_is_not_accepted(self):
        for value in (self.outsider.pk, 'abc', '-1', '9' * 30):
            response = self.page(a=self.current.pk, submitted_coder=value)
            self.assertEqual(response.context['submitted_tasks'].paginator.count, 0)
            self.assertEqual(response.context['submission_filter_error'], '请选择本轮有任务的编码员。')

    def test_page_links_and_filter_form_preserve_workbench_context(self):
        response = self.page(a=self.current.pk, page=1, submitted_coder=self.coder.pk,
                             unrelated='保留这个条件', tag=['A', 'B'])
        next_params = parse_qs(urlsplit(response.context['submission_next_url']).query)
        self.assertEqual(next_params, {'a': [str(self.current.pk)], 'page': ['1'],
            'submitted_coder': [str(self.coder.pk)], 'submitted_page': ['2'],
            'unrelated': ['保留这个条件'], 'tag': ['A', 'B']})
        fields = response.context['submission_context_fields']
        self.assertIn(('a', str(self.current.pk)), fields)
        self.assertIn(('page', '1'), fields)
        self.assertIn(('tag', 'A'), fields)
        self.assertIn(('tag', 'B'), fields)
        self.assertNotIn(('submitted_coder', str(self.coder.pk)), fields)
        second = self.page(**{key: values if len(values) > 1 else values[0]
                              for key, values in next_params.items()})
        self.assertEqual(second.context['assignment'].pk, self.current.pk)
        self.assertEqual(second.context['submitted_tasks'].number, 2)
        reset_params = parse_qs(urlsplit(response.context['submission_reset_url']).query)
        self.assertNotIn('submitted_coder', reset_params)
        self.assertNotIn('submitted_page', reset_params)
        self.assertEqual(reset_params['a'], [str(self.current.pk)])

    def test_search_without_explicit_current_assignment_pins_current_task(self):
        response = self.page(submitted_page=2)
        self.assertIn(('a', str(response.context['assignment'].pk)), response.context['submission_context_fields'])

    def test_invalid_pages_fall_back_without_losing_filter(self):
        for value in ('abc', '0', '9999999999999999999999999'):
            response = self.page(a=self.current.pk, submitted_coder=self.coder.pk, submitted_page=value)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['submitted_tasks'].paginator.count, 61)

    def test_lookup_get_never_changes_answers_history_or_round_state(self):
        before = self.state()
        self.page(a=self.current.pk, submitted_page=2)
        self.page(a=self.current.pk, submitted_unit=self.last.unit_id, submitted_coder=self.coder.pk)
        self.page(a=self.current.pk, submitted_unit='invalid')
        self.assertEqual(self.state(), before)

    def test_other_round_and_superseded_assignments_are_excluded(self):
        other_round = Round.objects.create(project=self.project, book=self.book, name='虚构另一轮', kind='formal')
        Assignment.objects.create(round=other_round, unit=self.last.unit, coder=self.owner,
            status='submitted', primary=self.first, revision=1)
        Assignment.objects.filter(pk=self.last.pk).update(superseded=True)
        response = self.page(a=self.current.pk, submitted_unit=self.last.unit_id)
        self.assertEqual(response.context['submitted_tasks'].paginator.count, 0)

    def test_coder_viewer_and_other_project_user_do_not_get_management_panel(self):
        for user in (self.coder, self.viewer):
            self.client.force_login(user)
            response = self.page(submitted_unit=self.last.unit_id)
            self.assertNotContains(response, 'id="submission-controls"')
        self.client.force_login(self.outsider)
        self.assertEqual(self.page(submitted_unit=self.last.unit_id).status_code, 404)

    def test_reviewer_gets_panel_but_review_or_closed_round_does_not(self):
        reviewer = get_user_model().objects.create_user('fictional_paging_reviewer')
        Membership.objects.create(project=self.project, user=reviewer, role='reviewer')
        self.client.force_login(reviewer)
        self.assertContains(self.page(submitted_page=2), 'id="submission-controls"')
        for state in ('review', 'closed'):
            Round.objects.filter(pk=self.r.pk).update(state=state)
            self.assertNotContains(self.page(submitted_page=2), 'id="submission-controls"')

    def test_existing_reopen_post_still_checks_reason_revision_and_csrf(self):
        url = f'/assignments/{self.last.pk}/reopen/'
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(url, {'revision': self.last.revision, 'reason': '虚构纠错'}).status_code, 403)
        self.client.post(url, {'revision': self.last.revision, 'reason': ''})
        self.last.refresh_from_db()
        self.assertEqual(self.last.status, 'submitted')
        self.client.post(url, {'revision': self.last.revision - 1, 'reason': '虚构纠错'})
        self.last.refresh_from_db()
        self.assertEqual(self.last.status, 'submitted')
        response = self.client.post(url, {'revision': self.last.revision, 'reason': '虚构纠错'})
        self.last.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.last.status, 'draft')
        self.assertEqual(self.last.note, 'PEER_PRIVATE_NOTE_DO_NOT_SHOW')
        self.assertEqual(self.last.primary_id, self.second.pk)
        self.assertTrue(AuditEvent.objects.filter(action='annotation.reopen', object_id=str(self.last.pk)).exists())

    def test_reopen_post_keeps_valid_lookup_page_and_current_task_open(self):
        response = self.page(a=self.current.pk, page=1, submitted_page=2, submitted_coder=self.coder.pk)
        values = {'return_' + name: value for name, value in response.context['submission_return_fields']}
        self.assertContains(response, 'name="return_submitted_page" value="2"')
        result = self.client.post(f'/assignments/{self.last.pk}/reopen/', {
            'revision': self.last.revision, 'reason': '虚构分页纠错', **values})
        parsed = urlsplit(result.url)
        self.assertEqual(parsed.path, self.url)
        self.assertEqual(parsed.fragment, 'submission-controls')
        self.assertEqual(parse_qs(parsed.query), {'a': [str(self.current.pk)], 'page': ['1'],
            'submitted_page': ['2'], 'submitted_coder': [str(self.coder.pk)]})
        returned = self.client.get(result.url)
        self.assertEqual(returned.context['assignment'].pk, self.current.pk)
        self.assertEqual(returned.context['submitted_tasks'].number, 2)
        self.assertTrue(returned.context['submission_filters_active'])

    def test_reopen_last_matching_task_retains_empty_filter_without_validation_error(self):
        self.r.assignments.filter(coder=self.coder).exclude(pk=self.last.pk).update(status='draft')
        result = self.client.post(f'/assignments/{self.last.pk}/reopen/', {
            'revision': self.last.revision, 'reason': '虚构最后一条纠错', 'return_a': self.current.pk,
            'return_page': 1, 'return_submitted_page': 1, 'return_submitted_unit': self.last.unit_id,
            'return_submitted_coder': self.coder.pk})
        returned = self.client.get(result.url)
        self.assertEqual(returned.context['submitted_tasks'].paginator.count, 0)
        self.assertEqual(returned.context['submitted_unit_filter'], str(self.last.unit_id))
        self.assertEqual(returned.context['submitted_coder_filter'], str(self.coder.pk))
        self.assertFalse(returned.context['submission_filter_error'])
        self.assertContains(returned, '没有匹配的已提交任务')
        self.assertTrue(returned.context['submission_filters_active'])

    def test_failed_reopen_also_returns_to_valid_lookup_context(self):
        result = self.client.post(f'/assignments/{self.last.pk}/reopen/', {
            'revision': self.last.revision - 1, 'reason': '虚构版本冲突',
            'return_a': self.current.pk, 'return_submitted_unit': self.last.unit_id, 'return_submitted_page': 1})
        self.last.refresh_from_db()
        self.assertEqual(self.last.status, 'submitted')
        returned = self.client.get(result.url)
        self.assertEqual(returned.context['assignment'].pk, self.current.pk)
        self.assertEqual(returned.context['submitted_unit_filter'], str(self.last.unit_id))
        self.assertContains(returned, '任务状态已变化')

    def test_reopen_rejects_external_next_and_malformed_return_fields(self):
        result = self.client.post(f'/assignments/{self.last.pk}/reopen/', {
            'revision': self.last.revision - 1, 'reason': '虚构安全校验', 'next': 'https://external.invalid/',
            'return_a': '//external.invalid/', 'return_page': '-1', 'return_submitted_page': '9' * 30,
            'return_submitted_unit': 'javascript:alert(1)', 'return_submitted_coder': 'abc',
            'return_next': 'https://external.invalid/'})
        self.assertEqual(result.url, self.url)

    def test_reopen_drops_foreign_task_and_other_coder_current_selection(self):
        foreign_round = Round.objects.create(project=self.other, book=self.foreign_code.book,
            kind='formal', name='虚构外部返回目标')
        foreign_task = Assignment.objects.create(round=foreign_round, coder=self.outsider, unit=self.units[0])
        for task in (foreign_task, self.last):
            result = self.client.post(f'/assignments/{self.last.pk}/reopen/', {
                'revision': self.last.revision - 1, 'reason': '虚构安全校验', 'return_a': task.pk,
                'return_submitted_coder': self.outsider.pk, 'return_submitted_page': 1})
            self.assertEqual(urlsplit(result.url).path, self.url)
            params = parse_qs(urlsplit(result.url).query)
            self.assertNotIn('a', params)
            self.assertNotIn('submitted_coder', params)
            self.assertEqual(params['submitted_page'], ['1'])
