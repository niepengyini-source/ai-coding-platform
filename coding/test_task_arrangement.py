"""Signed task previews and selection APIs use synthetic, isolated test data only."""
import json
import random
import time
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.db.models import F
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Assignment, AuditEvent, Codebook, Membership, Round, SourceDocument, SourceRecord, Unit
from .services import create_round
from .task_arrangement import PLAN_SALT, pool_snapshot
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class TaskArrangementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)
        # A read-only preview must not freeze even an editable coding book.
        Codebook.objects.filter(pk=cls.book.pk).update(frozen=False)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)
        self.url = reverse('round_new', args=[self.project.pk])
        self.choices_url = reverse('round_unit_choices', args=[self.project.pk])

    def data(self, **changes):
        return {
            'action': 'preview', 'name': '虚构任务预览', 'kind': 'pilot',
            'book_id': self.book.pk, 'coder_ids': [self.owner.pk, self.coder.pk],
            'selection_mode': 'random', 'start_position': '', 'count': 2, 'seed': 17,
            'stratify': '', 'distribution': 'all', 'double_percent': 25,
            'unit_ids': '', 'manual_positions': '', **changes,
        }

    def state(self):
        book = Codebook.objects.get(pk=self.book.pk)
        return (Round.objects.count(), Assignment.objects.count(), AuditEvent.objects.count(),
                book.frozen, book.revision)

    def preview(self, **changes):
        response = self.client.post(self.url, self.data(**changes))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_preview.html')
        return response, response.context['preview_token']

    def confirm(self, token, **changes):
        return self.client.post(self.url, {'action': 'confirm', 'preview_token': token, **changes})

    def choices(self, **params):
        return self.client.post(self.choices_url, json.dumps(params), content_type='application/json')

    def add_unit(self, position, text=None, metadata=None, document=None):
        text = text or f'批量虚构材料{position}'
        record = SourceRecord.objects.create(document=document or self.doc, position=position,
                                            text=text, metadata=metadata or {'interview': 'C'})
        return Unit.objects.create(record=record, text=text, end=len(text))

    def foreign_unit(self):
        document = SourceDocument.objects.create(project=self.other, name='foreign-synthetic.csv',
            raw=b'text\nfictional\n', sha256='b' * 64, text_column='text')
        return self.add_unit(1, '其他虚构项目材料', document=document)

    def assert_rejected_without_tasks(self, token, message):
        before = self.state()
        response = self.confirm(token)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_form.html')
        self.assertContains(response, message)
        self.assertEqual(self.state(), before)
        return response

    def test_get_and_preview_do_not_create_tasks_freeze_rules_or_audit(self):
        before = self.state()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_form.html')
        self.assertEqual(self.state(), before)
        response, token = self.preview()
        payload = signing.loads(token, salt=PLAN_SALT)
        self.assertEqual(payload['actor'], self.owner.pk)
        self.assertEqual(payload['project'], self.project.pk)
        self.assertEqual(response.context['unit_count'], 2)
        self.assertEqual(self.state(), before)

    def test_three_modes_preview_then_confirm_the_exact_reviewed_units(self):
        cases = [
            ({'selection_mode': 'sequential', 'start_position': 2}, self.units[1:]),
            ({'selection_mode': 'manual', 'manual_positions': '1、3', 'count': 99},
             [self.units[0], self.units[2]]),
            ({'selection_mode': 'random'}, random.Random(17).sample(self.units, 2)),
        ]
        for changes, expected in cases:
            with self.subTest(mode=changes['selection_mode']):
                before = self.state()
                response, token = self.preview(**changes)
                expected_ids = [unit.pk for unit in expected]
                self.assertEqual(self.state(), before)
                self.assertEqual([unit.pk for unit in response.context['units']], expected_ids)
                payload = signing.loads(token, salt=PLAN_SALT)
                self.assertEqual(payload['selected_ids'], expected_ids)
                preview_counts = {member.user_id: member.task_count for member in response.context['members']}
                response = self.confirm(token)
                self.assertEqual(response.status_code, 302)
                created = Round.objects.latest('pk')
                self.assertEqual(response.url, reverse('workbench', args=[created.pk]))
                self.assertEqual(created.sampling['selection_mode'], changes['selection_mode'])
                self.assertEqual(created.sampling['unit_ids'], expected_ids)
                self.assertEqual(created.sampling['selection_positions'],
                                 [self.units.index(unit) + 1 for unit in expected])
                self.assertEqual(created.sampling['arrangement_token'], payload['nonce'])
                self.assertEqual(created.sampling['pool_hash'], payload['pool_hash'])
                for user_id, count in preview_counts.items():
                    self.assertEqual(created.assignments.filter(coder_id=user_id).count(), count)
                    self.assertEqual(set(created.assignments.filter(coder_id=user_id)
                                         .values_list('unit_id', flat=True)), set(expected_ids))
                self.assertEqual(self.state()[0], before[0] + 1)
                self.assertEqual(self.state()[1], before[1] + 4)

    def test_preview_distribution_counts_match_confirmed_assignments(self):
        for distribution in ['distributed', 'mixed']:
            with self.subTest(distribution=distribution):
                response, token = self.preview(selection_mode='sequential', count=0,
                                                distribution=distribution, double_percent=67)
                counts = {member.user_id: member.task_count for member in response.context['members']}
                self.assertEqual(self.confirm(token).status_code, 302)
                created = Round.objects.latest('pk')
                self.assertEqual(created.assignments.count(), sum(counts.values()))
                for user_id, count in counts.items():
                    self.assertEqual(created.assignments.filter(coder_id=user_id).count(), count)

    def test_legacy_post_without_action_or_selection_mode_still_creates(self):
        data = self.data(count=1, unit_ids=f'{self.units[0].pk},{self.units[2].pk}')
        data.pop('action')
        data.pop('selection_mode')
        before = self.state()
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        created = Round.objects.latest('pk')
        self.assertEqual(self.state()[0], before[0] + 1)
        self.assertEqual(created.sampling['selection_mode'], 'random')
        self.assertEqual(created.sampling['specified_pool_ids'], [self.units[0].pk, self.units[2].pk])
        self.assertEqual(created.sampling['unit_ids'],
                         [unit.pk for unit in random.Random(17).sample([self.units[0], self.units[2]], 1)])

    def test_confirm_replay_creates_one_round_and_freezes_only_once(self):
        _, token = self.preview()
        before = self.state()
        first = self.confirm(token)
        after_first = self.state()
        second = self.confirm(token)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.url, second.url)
        self.assertEqual(after_first, self.state())
        self.assertEqual(after_first[0], before[0] + 1)
        self.assertEqual(after_first[1], before[1] + 4)
        self.assertTrue(after_first[3])
        self.assertEqual(after_first[4], before[4] + 1)

    def test_edit_and_paginate_preview_are_read_only(self):
        for position in range(4, 46):
            self.add_unit(position)
        _, token = self.preview(count=0, selection_mode='sequential')
        before = self.state()
        response = self.client.post(self.url, {'action': 'page', 'preview_token': token, 'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_preview.html')
        self.assertEqual(response.context['units'].number, 2)
        self.assertEqual([unit.selection_position for unit in response.context['units']], list(range(41, 46)))
        response = self.client.post(self.url, {'action': 'edit', 'preview_token': token})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_form.html')
        self.assertTrue(response.context['form'].is_bound)
        self.assertEqual(response.context['form'].data['selection_mode'], 'sequential')
        self.assertEqual(self.state(), before)

    def test_tampered_missing_and_wrong_salt_tokens_are_rejected(self):
        _, token = self.preview()
        payload = signing.loads(token, salt=PLAN_SALT)
        for invalid in ['', token + 'x', signing.dumps(payload, salt='another-feature')]:
            with self.subTest(token=invalid[:12]):
                self.assert_rejected_without_tasks(invalid, '预览已过期或无效')

    def test_preview_expires_after_thirty_minutes(self):
        now = int(time.time())
        with patch('django.core.signing.time.time', return_value=now):
            _, token = self.preview()
        with patch('django.core.signing.time.time', return_value=now + 1801):
            self.assert_rejected_without_tasks(token, '预览已过期或无效')

    def test_signature_is_bound_to_the_original_actor(self):
        _, token = self.preview()
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        self.client.force_login(self.coder)
        before = self.state()
        self.assertEqual(self.confirm(token).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_signature_is_bound_to_the_original_project(self):
        _, token = self.preview()
        Membership.objects.create(project=self.other, user=self.owner, role='reviewer')
        before = self.state()
        response = self.client.post(reverse('round_new', args=[self.other.pk]),
                                    {'action': 'confirm', 'preview_token': token})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state(), before)

    def test_pool_growth_after_preview_requires_a_new_preview(self):
        _, token = self.preview()
        self.add_unit(4)
        self.assert_rejected_without_tasks(token, '材料或编码本已变化')

    def test_splitting_a_reviewed_unit_invalidates_the_plan(self):
        _, token = self.preview(selection_mode='manual', manual_positions='1')
        original = self.units[0]
        Unit.objects.filter(pk=original.pk).update(active=False)
        for start, end in [(0, 4), (4, original.end)]:
            Unit.objects.create(record=original.record, start=start, end=end,
                                text=original.text[start:end])
        self.assert_rejected_without_tasks(token, '材料或编码本已变化')

    def test_coding_book_revision_change_invalidates_the_plan(self):
        _, token = self.preview()
        Codebook.objects.filter(pk=self.book.pk).update(revision=F('revision') + 1)
        self.assert_rejected_without_tasks(token, '材料或编码本已变化')

    def test_new_current_book_invalidates_the_plan(self):
        _, token = self.preview()
        Codebook.objects.create(project=self.project, version=2, title='虚构新规则')
        self.assert_rejected_without_tasks(token, '材料或编码本已变化')

    def test_coder_membership_change_invalidates_the_plan(self):
        _, token = self.preview()
        Membership.objects.filter(project=self.project, user=self.coder).update(role='viewer')
        self.assert_rejected_without_tasks(token, '成员或可用材料有变化')

    def test_inactive_coder_invalidates_the_plan(self):
        _, token = self.preview()
        type(self.coder).objects.filter(pk=self.coder.pk).update(is_active=False)
        self.assert_rejected_without_tasks(token, '成员或可用材料有变化')

    def test_actor_permission_is_checked_again_on_confirmation(self):
        _, token = self.preview()
        Membership.objects.filter(project=self.project, user=self.owner).update(role='coder')
        before = self.state()
        self.assertEqual(self.confirm(token).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_changed_client_pool_hash_rejects_preview_without_creating(self):
        before = self.state()
        response = self.client.post(self.url, self.data(pool_hash='not-the-current-pool'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_form.html')
        self.assertContains(response, '材料已变化')
        self.assertEqual(self.state(), before)

    def test_recomputed_selection_must_match_the_signed_review(self):
        _, token = self.preview()
        payload = signing.loads(token, salt=PLAN_SALT)
        payload['selected_ids'] = []
        self.assert_rejected_without_tasks(signing.dumps(payload, salt=PLAN_SALT), '选取结果已变化')

    def test_empty_selected_api_filter_means_zero_not_the_whole_pool(self):
        before = self.state()
        response = self.choices(selected=[], all=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total_units'], 3)
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['results'], [])
        self.assertEqual(data['ids'], [])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.state(), before)

    def test_filtered_api_keeps_original_material_positions(self):
        response = self.choices(q='检查', all=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ids'], [self.units[1].pk])
        self.assertEqual(response.json()['results'][0]['position'], 2)
        response = self.choices(metadata_field='interview', metadata_value='B', all=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ids'], [self.units[2].pk])
        self.assertEqual(response.json()['results'][0]['position'], 3)
        response = self.choices(selected=[self.units[2].pk, self.units[0].pk])
        self.assertEqual([item['position'] for item in response.json()['results']], [1, 3])

    def test_api_all_returns_all_filtered_ids_not_only_the_current_page(self):
        extra = [self.add_unit(position) for position in range(4, 86)]
        expected = [unit.pk for unit in extra]
        response = self.choices(q='批量虚构', all=True, page=2)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 82)
        self.assertEqual(len(data['results']), 40)
        self.assertEqual(data['ids'], expected)
        self.assertEqual([item['position'] for item in data['results']], list(range(44, 84)))
        self.assertEqual(data['pool_hash'], pool_snapshot(self.project)[1])

    def test_api_document_filter_only_returns_that_projects_document(self):
        document = SourceDocument.objects.create(project=self.project, name='second-fictional.csv',
            raw=b'text\nsecond\n', sha256='c' * 64, text_column='text')
        unit = self.add_unit(1, '第二份虚构材料', document=document)
        response = self.choices(document=str(document.pk), all=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ids'], [unit.pk])
        self.assertEqual(response.json()['results'][0]['position'], 4)
        foreign = self.foreign_unit()
        response = self.choices(document=str(foreign.record.document_id), all=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ids'], [])

    def test_api_rejects_foreign_inactive_and_noninteger_selected_ids(self):
        foreign = self.foreign_unit()
        Unit.objects.filter(pk=self.units[0].pk).update(active=False)
        for selected in [[foreign.pk], [self.units[0].pk], [999999999], [True], ['2'], None]:
            with self.subTest(selected=selected):
                response = self.choices(selected=selected)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json())

    def test_api_invalid_payloads_and_filters_return_safe_errors(self):
        for params in [[], {'q': []}, {'document': '-1'}, {'metadata_field': 'not-a-field'},
                       {'metadata_value': 'A'}, {'selected': [self.units[0].pk] * 10001}]:
            with self.subTest(params=str(params)[:60]):
                response = self.client.post(self.choices_url, json.dumps(params), content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json())
        self.assertEqual(self.client.post(self.choices_url, '{bad json', content_type='application/json').status_code, 400)

    def test_login_and_project_roles_protect_form_preview_and_api(self):
        before = self.state()
        for user, status in [(self.coder, 403), (self.viewer, 403), (self.outsider, 404)]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, status)
                self.assertEqual(self.client.post(self.url, self.data()).status_code, status)
                self.assertEqual(self.choices().status_code, status)
        self.client.logout()
        for url in [self.url, self.choices_url]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/login/', response.url)
        self.assertEqual(self.state(), before)

    def test_cross_project_url_and_unsupported_api_method_are_rejected(self):
        before = self.state()
        self.assertEqual(self.client.get(reverse('round_new', args=[self.other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('round_unit_choices', args=[self.other.pk])).status_code, 404)
        self.assertEqual(self.client.put(self.choices_url, '{}', content_type='application/json').status_code, 405)
        self.assertEqual(self.state(), before)

    def test_csrf_protects_preview_confirm_and_json_selection_posts(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        _, token = self.preview()
        before = self.state()
        self.assertEqual(client.post(self.url, self.data()).status_code, 403)
        self.assertEqual(client.post(self.url, {'action': 'confirm', 'preview_token': token}).status_code, 403)
        self.assertEqual(client.post(self.choices_url, '{}', content_type='application/json').status_code, 403)
        self.assertEqual(client.get(self.url).status_code, 200)
        csrf = client.cookies['csrftoken'].value
        response = client.post(self.choices_url, json.dumps({'selected': []}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        response = client.post(self.url, self.data(), HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_preview.html')
        self.assertEqual(self.state(), before)

    def test_reviewer_can_preview_and_confirm_without_own_assignments(self):
        Membership.objects.create(project=self.project, user=self.outsider, role='reviewer')
        self.client.force_login(self.outsider)
        _, token = self.preview(coder_ids=[self.coder.pk])
        response = self.confirm(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('progress', args=[self.project.pk]))

    def test_duplicate_coders_are_deduplicated_before_preview_and_creation(self):
        response, token = self.preview(coder_ids=[self.owner.pk, self.coder.pk, self.owner.pk])
        self.assertEqual(response.context['plan']['coder_ids'], [self.owner.pk, self.coder.pk])
        self.assertEqual(self.confirm(token).status_code, 302)
        created = Round.objects.latest('pk')
        self.assertEqual(created.sampling['coder_ids'], [self.owner.pk, self.coder.pk])
        self.assertEqual(created.assignments.count(), 4)

    def test_mixed_distribution_requires_two_different_coders(self):
        before = self.state()
        response = self.client.post(self.url, self.data(coder_ids=[self.owner.pk, self.owner.pk], distribution='mixed'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coding/round_form.html')
        self.assertIn('coder_ids', response.context['form'].errors)
        self.assertContains(response, '至少两位编码员')
        self.assertEqual(self.state(), before)

    def test_service_rejects_duplicate_coders_before_creating_or_freezing(self):
        before = self.state()
        with self.assertRaisesMessage(ValueError, '编码员不能重复'):
            create_round(self.project, self.owner, self.book.pk, '虚构重复成员任务', 'pilot',
                         [self.owner.pk, self.owner.pk], 2, 17, 'mixed', 25)
        self.assertEqual(self.state(), before)
