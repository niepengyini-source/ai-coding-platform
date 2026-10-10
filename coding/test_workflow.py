"""New workflow checks use only fictional records and an isolated database."""
import json
import uuid
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.core.cache import cache
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Assignment, AuditEvent, Code, Codebook, Decision, Membership, Round, Unit
from .round_forms import RoundForm
from .services import annotation_data, create_round, project_archive, save_assignment
from .tests import TEST_SETTINGS, fixture
from .workflow import close_quick_coding, start_quick_coding


class WorkflowFixture:
    def prepare(self):
        cache.clear()
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)
        self.quick_url = f'/p/{self.project.pk}/quick-start/'
        self.history_url = f'/p/{self.project.pk}/history/'
        self.progress_url = f'/p/{self.project.pk}/progress/'
        self.progress_api = f'/api/projects/{self.project.pk}/progress/'

    def quick(self, user=None, token=None, **extra):
        self.client.force_login(user or self.coder)
        token = token or str(uuid.uuid4())
        data = {'book_id': self.book.pk, 'token': token, **extra}
        response = self.client.post(self.quick_url, data)
        return response, data

    def save(self, a, action='save', **values):
        payload = {'revision': a.revision, 'primary': self.first.pk, 'secondary': [],
                   'uncertain': False, 'no_code': False, 'note': '虚构：结合上下文判断',
                   'action': action, 'token': str(uuid.uuid4()), **values}
        result = save_assignment(a.pk, a.coder, payload)
        a.refresh_from_db()
        return result


@override_settings(**TEST_SETTINGS)
class QuickStartTests(WorkflowFixture, TestCase):
    def setUp(self):
        self.prepare()

    def test_get_is_read_only_and_form_is_small(self):
        count = Round.objects.count()
        self.book.refresh_from_db()
        revision = self.book.revision
        response = self.client.get(self.quick_url)
        self.assertContains(response, '不需要填写任务安排表')
        self.assertNotContains(response, 'name="coder_ids"')
        self.assertEqual(Round.objects.count(), count)
        self.book.refresh_from_db()
        self.assertEqual(self.book.revision, revision)

    def test_member_can_start_only_their_own_batch_in_source_order(self):
        response, _ = self.quick(coder_ids=[self.owner.pk], kind='formal', count=1)
        self.assertEqual(response.status_code, 302)
        r = Round.objects.get(kind='quick')
        self.assertEqual(r.started_by, self.coder)
        self.assertEqual(list(r.assignments.values_list('coder_id', flat=True)), [self.coder.pk] * 3)
        self.assertEqual(list(r.assignments.order_by('id').values_list('unit_id', flat=True)), [u.pk for u in self.units])
        self.assertEqual(r.sampling['method'], '个人快速开始：全部有效单元，按原文顺序')

    def test_post_replay_and_second_tab_resume_without_duplicates(self):
        _, data = self.quick()
        first = Round.objects.get(kind='quick')
        self.client.post(self.quick_url, data)
        self.quick()
        self.assertEqual(Round.objects.filter(kind='quick').count(), 1)
        self.assertEqual(first.assignments.count(), 3)
        self.assertEqual(AuditEvent.objects.filter(action='quick.start').count(), 1)

    def test_start_freezes_new_book_and_uses_only_active_units(self):
        b = Codebook.objects.create(project=self.project, version=2, title='新的草稿')
        Code.objects.create(book=b, key='A', name='代码A', definition='虚构定义')
        self.units[0].active = False
        self.units[0].save()
        response, _ = self.quick(book_id=b.pk)
        self.assertEqual(response.status_code, 302)
        b.refresh_from_db()
        self.assertTrue(b.frozen)
        self.assertEqual(Round.objects.get(kind='quick').assignments.count(), 2)

    def test_resume_does_not_add_new_material_to_old_batch(self):
        self.quick()
        r = Round.objects.get(kind='quick')
        original = self.units[0]
        Unit.objects.create(record=original.record, start=0, end=1, text='虚构新单元')
        self.quick()
        self.assertEqual(r.assignments.count(), 3)

    def test_empty_book_or_missing_material_is_rejected_without_partial_round(self):
        empty = Codebook.objects.create(project=self.project, version=2)
        response, _ = self.quick(book_id=empty.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Round.objects.filter(kind='quick').count(), 0)
        Unit.objects.filter(record__document__project=self.project).update(active=False)
        response, _ = self.quick()
        self.assertContains(response, '请先导入材料')
        self.assertEqual(Round.objects.filter(kind='quick').count(), 0)

    def test_foreign_book_and_invalid_token_are_rejected(self):
        response, _ = self.quick(book_id=self.foreign_code.book_id)
        self.assertEqual(response.status_code, 200)
        response, _ = self.quick(token='not-a-uuid')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Round.objects.filter(kind='quick').exists())

    def test_viewer_and_outsider_cannot_start(self):
        response, _ = self.quick(user=self.viewer)
        self.assertEqual(response.status_code, 403)
        response, _ = self.quick(user=self.outsider)
        self.assertEqual(response.status_code, 404)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.coder)
        response = client.post(self.quick_url, {'book_id': self.book.pk, 'token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)

    def test_quick_kind_cannot_be_used_for_multi_member_assignment(self):
        form = RoundForm(project=self.project)
        self.assertNotIn('quick', dict(form.fields['kind'].choices))
        with self.assertRaisesMessage(ValueError, '只能为本人'):
            create_round(self.project, self.owner, self.book.pk, '错误的个人批次', 'quick',
                         [self.owner.pk, self.coder.pk], 0, 2026, 'all', 0)

    def test_other_member_cannot_open_or_export_personal_batch(self):
        self.quick(user=self.owner)
        r = Round.objects.get(kind='quick')
        self.client.force_login(self.coder)
        self.assertNotIn(r, self.client.get(f'/p/{self.project.pk}/').context['rounds'])
        for path in [f'/r/{r.pk}/', f'/api/rounds/{r.pk}/progress/', f'/r/{r.pk}/export/mine/',
                     f'/p/{self.project.pk}/exports/?target=mine&round={r.pk}']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_personal_finish_requires_all_submissions_and_is_replay_safe(self):
        self.quick()
        r = Round.objects.get(kind='quick')
        url = f'/p/{self.project.pk}/quick/{r.pk}/finish/'
        self.client.post(url, {'revision': r.revision})
        r.refresh_from_db()
        self.assertEqual(r.state, 'active')
        for a in r.assignments.all():
            self.save(a, action='submit')
        data = {'revision': r.revision}
        self.client.post(url, data)
        self.client.post(url, data)
        r.refresh_from_db()
        self.assertEqual(r.state, 'closed')
        self.assertEqual(AuditEvent.objects.filter(action='quick.close').count(), 1)
        self.assertFalse(Decision.objects.filter(round=r).exists())

    def test_old_start_token_after_archive_does_not_create_new_batch(self):
        _, data = self.quick()
        r = Round.objects.get(kind='quick')
        for a in r.assignments.all():
            self.save(a, action='submit')
        close_quick_coding(r.pk, self.coder, r.revision)
        self.client.post(self.quick_url, data)
        self.assertEqual(Round.objects.filter(kind='quick').count(), 1)
        self.quick()
        self.assertEqual(Round.objects.filter(kind='quick').count(), 2)

    def test_finish_cannot_archive_other_persons_round_or_normal_round(self):
        self.quick(user=self.owner)
        r = Round.objects.get(kind='quick')
        self.client.force_login(self.coder)
        for round_id in [r.pk, self.r.pk]:
            response = self.client.post(f'/p/{self.project.pk}/quick/{round_id}/finish/', {'revision': 0})
            self.assertEqual(response.status_code, 404)

    def test_quick_batch_has_no_reliability_or_collaborative_state_actions(self):
        self.quick(user=self.owner)
        r = Round.objects.get(kind='quick')
        self.assertEqual(self.client.post(f'/r/{r.pk}/manage/', {'action': 'lock', 'revision': 0}).status_code, 403)
        self.assertEqual(self.client.get(f'/r/{r.pk}/report/').status_code, 403)

    def test_personal_archive_does_not_invent_final_consensus(self):
        self.quick()
        r = Round.objects.get(kind='quick')
        for a in r.assignments.all():
            self.save(a, action='submit')
        close_quick_coding(r.pk, self.coder, r.revision)
        self.assertEqual(self.client.get(f'/r/{r.pk}/export/final/').status_code, 403)
        with zipfile.ZipFile(io.BytesIO(project_archive(self.project))) as archive:
            self.assertIn(f'03_personal_coding/round_{r.pk}_independent.csv', archive.namelist())
            self.assertNotIn(f'06_final/round_{r.pk}.csv', archive.namelist())


@override_settings(**TEST_SETTINGS)
class ProgressTests(WorkflowFixture, TestCase):
    def setUp(self):
        self.prepare()

    def test_counts_separate_saved_submitted_and_unstarted(self):
        tasks = list(self.r.assignments.filter(coder=self.coder))
        self.save(tasks[0])
        self.save(tasks[1], action='submit')
        response = self.client.get(self.progress_api)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['my'], {'total': 3, 'submitted': 1, 'saved': 1, 'unstarted': 1,
                                                'remaining': 2, 'percent': 33.3})

    def test_member_sees_only_own_progress_without_labels_or_team_data(self):
        other = self.r.assignments.filter(coder=self.owner).first()
        self.save(other, note='SECRET_OTHER_PERSON_NOTE')
        response = self.client.get(self.progress_api)
        self.assertNotIn('team', response.json())
        self.assertNotIn('members', response.json())
        self.assertNotContains(response, 'SECRET_OTHER_PERSON_NOTE')
        self.assertNotContains(response, 'PLAN')
        self.assertNotContains(response, self.units[0].text)

    def test_owner_has_team_counts_but_no_answers(self):
        self.quick()
        self.client.force_login(self.owner)
        data = self.client.get(self.progress_api).json()
        self.assertEqual(data['my']['total'], 3)
        self.assertEqual(data['team']['total'], 9)
        self.assertEqual(len(data['members']), 2)
        self.assertNotIn('primary', json.dumps(data))

    def test_page_has_progress_and_history_entry(self):
        response = self.client.get(self.progress_url)
        self.assertContains(response, 'data-progress-endpoint')
        self.assertContains(response, '已保存草稿')
        self.assertContains(response, self.history_url)
        self.assertNotContains(response, '团队进度（负责人')

    def test_round_rows_are_paginated(self):
        for i in range(29):
            r = Round.objects.create(project=self.project, book=self.book, name='虚构批次' + str(i), kind='pilot')
            Assignment.objects.create(round=r, coder=self.coder, unit=self.units[0])
        self.assertEqual(len(self.client.get(self.progress_api).json()['rows']), 25)
        self.assertEqual(len(self.client.get(self.progress_api + '?page=2').json()['rows']), 5)

    def test_outsider_cannot_read_progress_and_anonymous_is_redirected(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.progress_api).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.progress_url).status_code, 302)


@override_settings(**TEST_SETTINGS)
class HistorySearchTests(WorkflowFixture, TestCase):
    def setUp(self):
        self.prepare()
        self.a = self.r.assignments.filter(coder=self.coder, unit=self.units[0]).get()
        self.b = self.r.assignments.filter(coder=self.coder, unit=self.units[1]).get()

    def history(self, **data):
        response = self.client.get(self.history_url, data)
        self.assertEqual(response.status_code, 200)
        return response

    def test_unstarted_assignments_are_not_history(self):
        self.assertEqual(self.history().context['page'].paginator.count, 0)

    def test_saved_own_records_only_never_other_coders_notes(self):
        self.save(self.a)
        other = self.r.assignments.filter(coder=self.owner).first()
        self.save(other, note='PRIVATE_OTHER_HISTORY')
        response = self.history()
        self.assertEqual(response.context['page'].paginator.count, 1)
        self.assertNotContains(response, 'PRIVATE_OTHER_HISTORY')

    def test_quick_keyword_is_fuzzy_but_exact_keyword_is_complete_match(self):
        self.save(self.a)
        self.assertEqual(self.history(q='目标').context['page'].paginator.count, 1)
        self.assertEqual(self.history(mode='exact', q='目标').context['page'].paginator.count, 0)
        self.assertEqual(self.history(mode='exact', q=self.units[0].text).context['page'].paginator.count, 1)

    def test_exact_filters_unit_record_code_file_version_status_and_round(self):
        self.save(self.a, action='submit')
        self.save(self.b, primary=self.second.pk)
        response = self.history(unit_id=self.units[0].pk, assignment_id=self.a.pk, code='plan',
                                filename=self.doc.name, book_version=1, status='submitted', round_id=self.r.pk)
        self.assertEqual(response.context['page'].paginator.count, 1)
        self.assertEqual(response.context['page'][0].pk, self.a.pk)
        self.assertEqual(self.history(code='PLA').context['page'].paginator.count, 0)

    def test_case_insensitive_keyword_and_suspicious_input_are_safe(self):
        self.save(self.a)
        self.assertEqual(self.history(q='plan').context['page'].paginator.count, 1)
        self.assertEqual(self.history(q="' OR 1=1 --").context['page'].paginator.count, 0)
        self.assertEqual(self.history(q='9' * 250).context['page'].paginator.count, 0)

    def test_time_range_uses_local_minutes_and_includes_end_minute(self):
        self.save(self.a)
        self.save(self.b)
        start = timezone.localtime().replace(hour=9, minute=30, second=0, microsecond=0)
        Assignment.objects.filter(pk=self.a.pk).update(updated_at=start + timedelta(seconds=59, microseconds=999999))
        Assignment.objects.filter(pk=self.b.pk).update(updated_at=start + timedelta(minutes=1))
        date = start.strftime('%Y-%m-%dT%H:%M')
        result = self.history(start=date, end=date)
        self.assertEqual([a.pk for a in result.context['page']], [self.a.pk])

    def test_last_seven_days_and_custom_time_override_preset(self):
        self.save(self.a)
        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        old = today - timedelta(days=8)
        Assignment.objects.filter(pk=self.a.pk).update(updated_at=old)
        self.assertEqual(self.history(period='week').context['page'].paginator.count, 0)
        result = self.history(period='week', start=old.strftime('%Y-%m-%dT%H:%M'), end=old.strftime('%Y-%m-%dT%H:%M'))
        self.assertEqual(result.context['page'].paginator.count, 1)

    def test_invalid_filters_never_fall_back_to_all_history(self):
        self.save(self.a)
        for data in [{'unit_id': 'not-an-id'}, {'start': 'not-a-date'},
                     {'start': '2026-10-09T10:00', 'end': '2026-10-08T10:00'}, {'round_id': 999999999}]:
            with self.subTest(data=data):
                response = self.history(**data)
                self.assertTrue(response.context['form'].errors)
                self.assertEqual(response.context['page'].paginator.count, 0)

    def test_own_notes_can_be_searched_and_uncertainty_filter_is_exact(self):
        self.save(self.a, uncertain=True, note='需要核对一个虚构边界例')
        self.assertEqual(self.history(q='边界例', uncertain='yes').context['page'].paginator.count, 1)
        self.assertEqual(self.history(uncertain='no').context['page'].paginator.count, 0)

    def test_saved_records_have_direct_correct_assignment_link(self):
        self.save(self.a)
        response = self.history()
        self.assertContains(response, f'href="/r/{self.r.pk}/?a={self.a.pk}">修改')

    def test_detail_has_before_after_and_original_rule_version(self):
        self.save(self.a, note='旧的虚构判断')
        self.save(self.a, primary=self.second.pk, note='新的虚构判断')
        Codebook.objects.create(project=self.project, version=2, title='当前新版')
        response = self.client.get(self.history_url + str(self.a.pk) + '/')
        self.assertContains(response, '旧的虚构判断')
        self.assertContains(response, '新的虚构判断')
        self.assertContains(response, 'PLAN · 计划')
        self.assertContains(response, '依据v1规则')

    def test_detail_cannot_read_other_person_or_project_record(self):
        other = self.r.assignments.filter(coder=self.owner).first()
        self.save(other)
        self.assertEqual(self.client.get(self.history_url + str(other.pk) + '/').status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.history_url).status_code, 404)

    def test_read_only_role_has_no_modification_links(self):
        self.save(self.a)
        Membership.objects.filter(project=self.project, user=self.coder).update(role='viewer')
        response = self.history()
        self.assertNotContains(response, '>修改 →')
        self.assertFalse(response.context['page'][0].history_flags['editable'])
        response = self.client.get(f'/r/{self.r.pk}/?a={self.a.pk}')
        self.assertContains(response, 'data-locked="true"')
        self.assertNotContains(response, 'data-action="save"')

    def test_pagination_preserves_search_and_date_filters(self):
        for i in range(28):
            r = Round.objects.create(project=self.project, book=self.book, name='搜索批次', kind='pilot')
            Assignment.objects.create(round=r, unit=self.units[0], coder=self.coder, revision=1, note='分页关键词')
        response = self.history(q='分页关键词', period='today')
        self.assertEqual(len(response.context['page']), 25)
        self.assertIn('page=2', response.context['next_url'])
        self.assertIn('period=today', response.context['next_url'])
        self.assertIn('q=', response.context['next_url'])


@override_settings(**TEST_SETTINGS)
class HistoryCorrectionTests(WorkflowFixture, TestCase):
    def setUp(self):
        self.prepare()
        self.quick()
        self.personal = Round.objects.get(kind='quick')
        self.a = self.personal.assignments.first()
        self.save(self.a, action='submit', note='要保留的旧判断')
        self.url = self.history_url + str(self.a.pk) + '/reopen/'

    def test_personal_submitted_record_can_be_reopened_without_losing_answer(self):
        old = annotation_data(self.a)
        response = self.client.post(self.url, {'revision': self.a.revision, 'reason': '虚构：修正误点的代码'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'/r/{self.personal.pk}/?a={self.a.pk}')
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'draft')
        self.assertEqual(self.a.note, old['note'])
        self.assertEqual(self.a.primary_id, old['primary'])
        self.assertEqual(self.a.revision, old['revision'] + 1)
        event = AuditEvent.objects.get(object_id=str(self.a.pk), action='annotation.reopen')
        self.assertEqual(event.before, old)
        self.assertEqual(event.reason, '虚构：修正误点的代码')

    def test_reason_is_required_and_stale_revision_does_not_reopen(self):
        for data in [{'revision': self.a.revision, 'reason': ''},
                     {'revision': self.a.revision - 1, 'reason': '过期页面'}]:
            self.assertEqual(self.client.post(self.url, data).status_code, 200)
            self.a.refresh_from_db()
            self.assertEqual(self.a.status, 'submitted')

    def test_old_save_request_cannot_overwrite_reopened_record(self):
        revision = self.a.revision
        self.client.post(self.url, {'revision': revision, 'reason': '虚构修订'})
        payload = {'revision': revision, 'primary': self.second.pk, 'secondary': [],
                   'uncertain': False, 'no_code': False, 'note': '过期请求不能覆盖',
                   'action': 'save', 'token': str(uuid.uuid4())}
        response = self.client.post(f'/api/assignments/{self.a.pk}/save/', json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.a.refresh_from_db()
        self.assertEqual(self.a.note, '要保留的旧判断')

    def test_regular_coder_cannot_reopen_collaborative_submission(self):
        a = self.r.assignments.filter(coder=self.coder).first()
        self.save(a, action='submit')
        response = self.client.post(self.history_url + str(a.pk) + '/reopen/', {'revision': a.revision, 'reason': '不能绕过团队锁定'})
        self.assertEqual(response.status_code, 403)
        a.refresh_from_db()
        self.assertEqual(a.status, 'submitted')

    def test_owner_can_return_own_collaborative_submission_during_active_stage(self):
        a = self.r.assignments.filter(coder=self.owner).first()
        self.save(a, action='submit')
        self.client.force_login(self.owner)
        response = self.client.post(self.history_url + str(a.pk) + '/reopen/', {'revision': a.revision, 'reason': '负责人修订自己的记录'})
        self.assertEqual(response.status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.status, 'draft')

    def test_archive_remains_read_only_even_for_personal_starter(self):
        for a in self.personal.assignments.exclude(pk=self.a.pk):
            self.save(a, action='submit')
        close_quick_coding(self.personal.pk, self.coder, self.personal.revision)
        response = self.client.post(self.url, {'revision': self.a.revision, 'reason': '不能改归档'})
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'submitted')
        self.assertContains(response, '已经归档')

    def test_post_requires_csrf_and_get_does_not_mutate(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.coder)
        self.assertEqual(client.post(self.url, {'revision': self.a.revision, 'reason': '虚构'}).status_code, 403)


@override_settings(**TEST_SETTINGS)
class QuickStartRaceTests(WorkflowFixture, TransactionTestCase):
    def setUp(self):
        self.prepare()

    def concurrent_starts(self, users):
        barrier = Barrier(len(users))
        def worker(user):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                r, _ = start_quick_coding(self.project, user, self.book.pk, str(uuid.uuid4()))
                return r.pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(users)) as pool:
            return list(pool.map(worker, users))

    def test_same_user_two_simultaneous_starts_create_one_batch(self):
        results = self.concurrent_starts([self.coder, self.coder])
        self.assertEqual(results[0], results[1])
        self.assertEqual(Round.objects.filter(kind='quick').count(), 1)
        self.assertEqual(Assignment.objects.filter(round__kind='quick').count(), 3)

    def test_different_members_have_isolated_personal_batches(self):
        results = self.concurrent_starts([self.owner, self.coder])
        self.assertEqual(len(set(results)), 2)
        for r in Round.objects.filter(kind='quick'):
            self.assertEqual(set(r.assignments.values_list('coder_id', flat=True)), {r.started_by_id})
