"""Undo uses fictional assignments in the isolated test database, never research files."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

from .book_updates import apply_book_update, begin_update
from .models import Assignment, AuditEvent, Comment, Decision, Membership, SourceRecord, Unit
from .services import Conflict, annotation_data, save_assignment
from .splitting import restore_personal_split, split_personal, split_restore_options
from .tests import TEST_SETTINGS, fixture
from .workflow import coding_assignments, start_quick_coding


class RestoreFixtures:
    def setup_restore(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.personal, _ = start_quick_coding(self.project, self.coder, self.book.pk, uuid.uuid4())
        self.a = self.personal.assignments.get(unit=self.units[0])

    def split(self, a=None, **changes):
        a = a or self.a
        result = split_personal(a.pk, self.coder, {'cuts': [2, 5], 'revision': a.revision,
                               'token': str(uuid.uuid4()), **changes})
        a.refresh_from_db()
        event = AuditEvent.objects.filter(action='annotation.split', object_id=str(a.pk)).latest('id')
        return list(a.segments.filter(pk__in=event.after['assignments']).select_related('unit').order_by('unit__start'))

    def save(self, a, **changes):
        result = save_assignment(a.pk, self.coder, {'revision': a.revision, 'action': 'save',
            'token': str(uuid.uuid4()), 'primary': self.first.pk, 'secondary': [],
            'uncertain': False, 'no_code': False, 'note': '虚构判断', **changes})
        a.refresh_from_db()
        return result

    def payload(self, child, parent=None, **changes):
        options = split_restore_options(child, self.coder)
        option = next((item for item in options if item['parent_id'] == parent.pk), None) if parent else options[0]
        return {**option, 'revision': child.revision, 'token': str(uuid.uuid4()),
                'book_id': self.book.pk, **changes}

    def restore(self, child, data=None, **changes):
        data = data or self.payload(child)
        response = self.client.post(f'/api/assignments/{child.pk}/unsplit/', json.dumps({**data, **changes}),
                                    content_type='application/json')
        return response

    def state(self):
        return (list(Assignment.objects.order_by('pk').values()), list(Unit.objects.order_by('pk').values()),
                list(SourceRecord.objects.order_by('pk').values()), AuditEvent.objects.count())


@override_settings(**TEST_SETTINGS)
class SplitRestoreTests(RestoreFixtures, TestCase):
    def setUp(self):
        self.setup_restore()
        self.client.force_login(self.coder)

    def test_undo_restores_original_and_preserves_units_and_source_metadata(self):
        record = SourceRecord.objects.values().get(pk=self.a.unit.record_id)
        children = self.split()
        before_unit_count, before_assignment_count = Unit.objects.count(), Assignment.objects.count()
        response = self.restore(children[0])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['next_url'], f'/r/{self.personal.pk}/?a={self.a.pk}')
        self.a.refresh_from_db()
        self.assertFalse(self.a.superseded)
        self.assertEqual(self.a.revision, 2)
        self.assertEqual(self.a.status, 'draft')
        self.assertFalse(self.a.primary_id)
        self.assertEqual(self.a.segments.filter(superseded=True).count(), 3)
        self.assertEqual(Unit.objects.count(), before_unit_count)
        self.assertEqual(Assignment.objects.count(), before_assignment_count)
        self.assertEqual(SourceRecord.objects.values().get(pk=self.a.unit.record_id), record)
        self.assertEqual(list(coding_assignments(self.personal, self.coder).values_list('unit_id', flat=True)),
                         [unit.pk for unit in self.units])
        self.assertTrue(all(unit.active for unit in Unit.objects.filter(pk__in=[unit.pk for unit in self.units])))

    def test_saved_parent_labels_note_and_flags_survive_split_restore(self):
        self.book.policy = 'multi'
        self.book.save(update_fields=['policy'])
        self.save(self.a, secondary=[self.second.pk], uncertain=True, note='原先依据')
        before = annotation_data(self.a)
        children = self.split()
        self.assertEqual(self.restore(children[0]).status_code, 200)
        self.a.refresh_from_db()
        for field in ['primary', 'secondary', 'uncertain', 'no_code', 'note', 'status']:
            self.assertEqual(annotation_data(self.a)[field], before[field])

    def test_submitted_parent_is_restored_submitted_without_silent_reopen(self):
        self.save(self.a, action='submit')
        children = self.split()
        self.assertEqual(self.restore(children[0]).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'submitted')
        page = self.client.get(f'/r/{self.personal.pk}/?a={self.a.pk}')
        self.assertTrue(page.context['annotation_locked'])
        self.assertEqual(self.a.primary_id, self.first.pk)

    def test_child_draft_requires_explicit_confirmation_and_retains_history(self):
        children = self.split()
        self.save(children[0], note='片段自己的判断')
        data = self.payload(children[1])
        self.assertTrue(data['requires_confirmation'])
        before = self.state()
        response = self.restore(children[1], data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.restore(children[1], data, confirm_coded=True).status_code, 200)
        children[0].refresh_from_db()
        self.assertTrue(children[0].superseded)
        self.assertEqual(children[0].note, '片段自己的判断')
        event = AuditEvent.objects.get(action='annotation.unsplit')
        saved = next(item for item in event.before['descendants'] if item['id'] == children[0].pk)
        self.assertEqual(saved['primary'], self.first.pk)
        self.assertEqual(saved['note'], '片段自己的判断')
        self.assertEqual(AuditEvent.objects.filter(action='annotation.save', object_id=str(children[0].pk)).count(), 1)

    def test_submitted_child_requires_confirmation_and_keeps_submission(self):
        children = self.split()
        self.save(children[0], action='submit')
        self.assertEqual(self.restore(children[1]).status_code, 409)
        self.assertEqual(self.restore(children[1], confirm_coded=True).status_code, 200)
        children[0].refresh_from_db()
        self.assertEqual(children[0].status, 'submitted')
        self.assertEqual(children[0].primary_id, self.first.pk)

    def test_blank_saved_draft_is_still_history_requiring_confirmation(self):
        child = self.split()[0]
        self.save(child, primary=None, note='')
        self.assertEqual(self.restore(child).status_code, 409)
        self.assertEqual(self.restore(child, confirm_coded=True).status_code, 200)

    def test_current_form_flush_does_not_invalidate_branch_fingerprint(self):
        child = self.split()[0]
        data = self.payload(child)
        self.save(child)
        self.assertEqual(data['branch_token'], self.payload(child)['branch_token'])
        self.assertEqual(self.restore(child, data, revision=child.revision, confirm_coded=True).status_code, 200)

    def test_sibling_edit_after_preview_rejects_without_writes(self):
        children = self.split()
        data = self.payload(children[0], confirm_coded=True)
        self.save(children[1])
        before = self.state()
        response = self.restore(children[0], data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.restore(children[0], confirm_coded=True).status_code, 200)

    def test_stale_current_and_parent_revisions_reject_without_writes(self):
        child = self.split()[0]
        data = self.payload(child)
        for changes in [{'revision': 999}, {'parent_revision': 999}, {'revision': True},
                        {'parent_revision': '1'}, {'revision': None}, {'parent_revision': None}]:
            with self.subTest(changes=changes):
                before = self.state()
                self.assertEqual(self.restore(child, data, **changes).status_code, 409)
                self.assertEqual(self.state(), before)

    def test_invalid_payloads_reject_without_writes(self):
        child = self.split()[0]
        data = self.payload(child)
        for changes in [{'token': 'bad'}, {'token': None}, {'parent_id': True}, {'parent_id': -1},
                        {'parent_id': '1'}, {'branch_token': None}, {'branch_token': ''},
                        {'confirm_coded': 'true'}, {'confirm_coded': 1}]:
            with self.subTest(changes=changes):
                before = self.state()
                self.assertEqual(self.restore(child, data, **changes).status_code, 400)
                self.assertEqual(self.state(), before)

    def test_repeated_identical_restore_is_idempotent(self):
        child = self.split()[0]
        data = self.payload(child)
        response = self.restore(child, data)
        self.assertEqual(response.status_code, 200)
        before = self.state()
        replay = self.restore(child, data)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), response.json())
        self.assertEqual(self.state(), before)
        self.assertEqual(AuditEvent.objects.filter(action='annotation.unsplit').count(), 1)

    def test_same_restore_token_cannot_change_payload(self):
        child = self.split()[0]
        data = self.payload(child)
        self.assertEqual(self.restore(child, data).status_code, 200)
        before = self.state()
        self.assertEqual(self.restore(child, data, confirm_coded=True).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_replay_cannot_revert_newer_parent_edits(self):
        child = self.split()[0]
        data = self.payload(child)
        self.assertEqual(self.restore(child, data).status_code, 200)
        self.a.refresh_from_db()
        self.save(self.a, note='撤回后的新判断')
        before = self.state()
        self.assertEqual(self.restore(child, data).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_nested_splits_can_undo_nearest_then_ancestor(self):
        children = self.split()
        grandchildren = self.split(children[1], cuts=[1])
        options = split_restore_options(grandchildren[0], self.coder)
        self.assertEqual([option['parent_id'] for option in options], [children[1].pk, self.a.pk])
        self.assertEqual(self.restore(grandchildren[0]).status_code, 200)
        children[1].refresh_from_db()
        self.assertFalse(children[1].superseded)
        self.assertEqual(self.personal.assignments.filter(superseded=False).count(), 5)
        self.assertEqual(self.restore(children[1], confirm_coded=True).status_code, 200)
        self.a.refresh_from_db()
        self.assertFalse(self.a.superseded)
        self.assertEqual(self.personal.assignments.filter(superseded=False).count(), 3)
        self.assertTrue(all(entry.superseded for entry in self.a.segments.all()))

    def test_nested_split_can_restore_original_directly(self):
        children = self.split()
        grandchildren = self.split(children[1], cuts=[1])
        self.save(grandchildren[1])
        data = self.payload(grandchildren[0], parent=self.a, confirm_coded=True)
        self.assertEqual(self.restore(grandchildren[0], data).status_code, 200)
        self.a.refresh_from_db()
        self.assertFalse(self.a.superseded)
        self.assertEqual(self.personal.assignments.filter(superseded=False).count(), 3)
        self.assertEqual(self.personal.assignments.filter(parent__isnull=False, superseded=False).count(), 0)

    def test_resplit_same_spans_creates_new_generation_and_replays_only_latest(self):
        first_children = self.split()
        self.save(first_children[0], note='旧片段判断')
        self.assertEqual(self.restore(first_children[1], confirm_coded=True).status_code, 200)
        self.a.refresh_from_db()
        data = {'cuts': [2, 5], 'revision': self.a.revision, 'token': str(uuid.uuid4())}
        second_children = self.split(**data)
        self.assertTrue(set(child.unit_id for child in first_children).isdisjoint(child.unit_id for child in second_children))
        self.assertTrue(all(child.superseded for child in Assignment.objects.filter(pk__in=[c.pk for c in first_children])))
        self.assertTrue(all(child.revision == 0 and not child.note for child in second_children))
        count = Assignment.objects.count()
        split_personal(self.a.pk, self.coder, data)
        self.assertEqual(Assignment.objects.count(), count)
        self.assertEqual(self.restore(second_children[0]).status_code, 200)
        first_children[0].refresh_from_db()
        self.assertEqual(first_children[0].note, '旧片段判断')

    def test_old_split_and_undo_replays_do_not_affect_new_generation(self):
        split_data = {'cuts': [2, 5], 'revision': 0, 'token': str(uuid.uuid4())}
        first_children = self.split(**split_data)
        undo_data = self.payload(first_children[0])
        self.assertEqual(self.restore(first_children[0], undo_data).status_code, 200)
        self.a.refresh_from_db()
        self.split()
        before = self.state()
        with self.assertRaises(Conflict):
            split_personal(self.a.pk, self.coder, split_data)
        self.assertEqual(self.restore(first_children[0], undo_data).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_old_split_token_cannot_be_reused_with_new_revision_after_undo(self):
        data = {'cuts': [2, 5], 'revision': 0, 'token': str(uuid.uuid4())}
        child = self.split(**data)[0]
        self.assertEqual(self.restore(child).status_code, 200)
        self.a.refresh_from_db()
        before = self.state()
        with self.assertRaises(Conflict):
            split_personal(self.a.pk, self.coder, {**data, 'revision': self.a.revision})
        self.assertEqual(self.state(), before)

    def test_rule_update_after_split_restores_submitted_parent_as_draft(self):
        self.save(self.a, action='submit')
        child = self.split()[0]
        before = self.payload(child)
        self.book.refresh_from_db()
        updated = begin_update(self.project, self.owner, self.book, self.book.revision)
        self.personal.refresh_from_db()
        apply_book_update(self.project, self.owner, updated, updated.revision,
                          {self.personal.pk: self.personal.revision})
        before_counts = self.state()
        self.assertEqual(self.restore(child, before).status_code, 409)
        self.assertEqual(self.state(), before_counts)
        child = Assignment.objects.select_related('unit', 'round').get(pk=child.pk)
        data = self.payload(child, book_id=updated.pk)
        self.assertEqual(self.restore(child, data).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'draft')
        self.assertEqual(self.a.primary_id, updated.codes.get(key=self.first.key).pk)
        self.assertEqual(self.a.note, '虚构判断')
        event = AuditEvent.objects.get(action='annotation.unsplit')
        self.assertEqual((event.before['status'], event.after['status']), ('submitted', 'draft'))

    def test_rule_update_before_split_does_not_reopen_newly_confirmed_parent(self):
        self.book.refresh_from_db()
        updated = begin_update(self.project, self.owner, self.book, self.book.revision)
        self.personal.refresh_from_db()
        apply_book_update(self.project, self.owner, updated, updated.revision,
                          {self.personal.pk: self.personal.revision})
        self.a.refresh_from_db()
        primary = updated.codes.get(key=self.first.key)
        self.save(self.a, primary=primary.pk, book_id=updated.pk, action='submit')
        child = self.split(book_id=updated.pk)[0]
        self.assertEqual(self.restore(child, book_id=updated.pk).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'submitted')
        self.assertEqual(self.a.primary_id, primary.pk)

    def test_shared_resplit_reuses_peer_units_preserves_old_answers_and_rejects_old_page(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        children = self.split(mine)
        split_personal(other.pk, self.owner, {'cuts': [2, 5], 'revision': other.revision,
                                             'token': str(uuid.uuid4())})
        peers = list(other.segments.select_related('unit').order_by('unit__start'))
        self.save(children[0], note='先前片段的完整判断', uncertain=True, action='submit')
        old_page = {'revision': children[0].revision, 'token': children[0].last_token,
            'primary': self.first.pk, 'secondary': [], 'uncertain': False, 'no_code': False,
            'note': '旧页面不能覆盖新代次', 'action': 'save'}
        self.assertEqual(self.restore(children[1], confirm_coded=True).status_code, 200)
        mine.refresh_from_db()
        peer_before = list(Assignment.objects.filter(coder=self.owner).order_by('pk').values())
        fresh = self.split(mine)
        self.assertEqual([entry.unit_id for entry in fresh], [entry.unit_id for entry in peers])
        self.assertEqual([entry.pk for entry in fresh], [entry.pk for entry in children])
        self.assertEqual(list(Assignment.objects.filter(coder=self.owner).order_by('pk').values()), peer_before)
        self.assertTrue(all(not entry.primary_id and not entry.secondary and not entry.note and
            not entry.uncertain and not entry.no_code and entry.status == 'draft' for entry in fresh))
        event = AuditEvent.objects.get(action='annotation.resplit', object_id=str(children[0].pk))
        self.assertEqual(event.before['primary'], self.first.pk)
        self.assertEqual(event.before['note'], '先前片段的完整判断')
        self.assertTrue(event.before['uncertain'])
        self.assertEqual(event.before['status'], 'submitted')
        self.assertEqual(event.before['parent_id'], mine.pk)
        self.assertEqual(event.before['unit']['text'], children[0].unit.text)
        self.assertGreater(event.after['revision'], old_page['revision'])
        response = self.client.post(f'/api/assignments/{fresh[0].pk}/save/', json.dumps(old_page),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 409)
        history = self.client.get(f'/p/{self.project.pk}/history/{fresh[0].pk}/')
        self.assertContains(history, '先前片段的完整判断')
        for entry in fresh + peers:
            save_assignment(entry.pk, entry.coder, {'revision': entry.revision, 'token': str(uuid.uuid4()),
                'action': 'submit', 'primary': self.first.pk, 'secondary': [], 'uncertain': False,
                'no_code': False, 'note': ''})
        from .reliability import report_for
        self.assertEqual(report_for(self.r)['pairs'][0]['n'], 3)

    def test_matching_peer_unit_used_by_another_parent_is_not_silently_reused(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        children = self.split(mine)
        split_personal(other.pk, self.owner, {'cuts': [2, 5], 'revision': other.revision,
                                             'token': str(uuid.uuid4())})
        self.assertEqual(self.restore(children[0]).status_code, 200)
        wrong_parent = self.r.assignments.get(coder=self.coder, unit=self.units[1])
        Assignment.objects.filter(pk=children[0].pk).update(parent=wrong_parent)
        mine.refresh_from_db()
        before = self.state()
        with self.assertRaises(Conflict):
            self.split(mine)
        self.assertEqual(self.state(), before)

    def test_foreign_user_viewer_removed_member_and_get_cannot_undo(self):
        child = self.split()[0]
        data = self.payload(child)
        before = self.state()
        self.assertEqual(self.client.get(f'/api/assignments/{child.pk}/unsplit/').status_code, 405)
        for user in [self.owner, self.outsider, self.viewer]:
            self.client.force_login(user)
            self.assertEqual(self.restore(child, data).status_code, 404)
            self.assertEqual(split_restore_options(child, user), [])
        self.client.force_login(self.coder)
        Membership.objects.filter(project=self.project, user=self.coder).update(role='viewer')
        self.assertEqual(self.restore(child, data).status_code, 403)
        self.assertEqual(split_restore_options(child, self.coder), [])
        Membership.objects.filter(project=self.project, user=self.coder).delete()
        self.assertEqual(self.restore(child, data).status_code, 404)
        self.assertEqual(self.state(), before)

    def test_other_coders_tasks_are_never_changed_or_returned(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        children = self.split(mine)
        before = Assignment.objects.values().get(pk=other.pk)
        self.assertEqual(self.restore(children[0]).status_code, 200)
        self.assertEqual(Assignment.objects.values().get(pk=other.pk), before)
        event = AuditEvent.objects.get(action='annotation.unsplit')
        self.assertTrue(all(item['id'] != other.pk for item in event.before['descendants']))

    def test_foreign_or_unrelated_parent_id_is_rejected(self):
        children = self.split()
        data = self.payload(children[0])
        other_parent = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        for parent_id in [999999, other_parent.pk, self.personal.assignments.get(unit=self.units[1]).pk]:
            before = self.state()
            self.assertEqual(self.restore(children[0], data, parent_id=parent_id).status_code, 409)
            self.assertEqual(self.state(), before)

    def test_round_lock_review_decision_comment_and_changed_rules_block_undo(self):
        children = self.split()
        data = self.payload(children[0])
        for state in ['review', 'closed']:
            self.personal.state = state
            self.personal.save(update_fields=['state'])
            self.assertEqual(self.restore(children[0], data).status_code, 409)
            children[0].round = self.personal
            self.assertEqual(split_restore_options(children[0], self.coder), [])
        self.personal.state = 'active'
        self.personal.save(update_fields=['state'])
        decision = Decision.objects.create(round=self.personal, unit=children[1].unit, author=self.owner, note='协商记录')
        self.assertEqual(self.restore(children[0], data).status_code, 409)
        self.assertEqual(split_restore_options(children[0], self.coder), [])
        decision.delete()
        comment = Comment.objects.create(round=self.personal, unit=children[2].unit, author=self.owner, body='讨论记录')
        self.assertEqual(self.restore(children[0], data).status_code, 409)
        comment.delete()
        self.assertEqual(self.restore(children[0], data, book_id=self.book.pk + 100).status_code, 409)

    def test_undo_counts_and_export_show_original_once_not_retired_fragments(self):
        children = self.split()
        self.save(children[0])
        self.assertEqual(self.restore(children[1], confirm_coded=True).status_code, 200)
        response = self.client.get(f'/api/rounds/{self.personal.pk}/progress/')
        self.assertEqual(response.json()['my_total'], 3)
        response = self.client.get(f'/r/{self.personal.pk}/export/mine/?format=json')
        self.assertEqual(response.status_code, 200)
        rows = response.json()['rows']
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(str(row['assignment_id']) == str(self.a.pk) for row in rows), 1)
        self.assertFalse(set(str(child.pk) for child in children).intersection(str(row['assignment_id']) for row in rows))
        self.assertEqual(self.client.get(f'/p/{self.project.pk}/history/{children[0].pk}/').status_code, 200)

    def test_csrf_and_json_payload_are_required(self):
        child = self.split()[0]
        data = self.payload(child)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.coder)
        self.assertEqual(csrf_client.post(f'/api/assignments/{child.pk}/unsplit/', json.dumps(data),
                                         content_type='application/json').status_code, 403)
        for raw in ['[]', 'null', '{']:
            self.assertEqual(self.client.post(f'/api/assignments/{child.pk}/unsplit/', raw,
                                             content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(f'/api/assignments/{child.pk}/unsplit/', data).status_code, 400)


@override_settings(**TEST_SETTINGS)
class ConcurrentSplitRestoreTests(RestoreFixtures, TransactionTestCase):
    def setUp(self):
        self.setup_restore()
        self.children = self.split()

    def race(self, actions):
        gate = Barrier(len(actions))

        def worker(action):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                try:
                    action()
                    return 'ok'
                except Conflict:
                    return 'conflict'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(worker, actions))

    def test_same_token_two_undo_requests_are_idempotent(self):
        data = self.payload(self.children[0])
        action = lambda: restore_personal_split(self.children[0].pk, self.coder, data)
        self.assertEqual(self.race([action, action]), ['ok', 'ok'])
        self.assertEqual(AuditEvent.objects.filter(action='annotation.unsplit').count(), 1)
        self.assertEqual(self.personal.assignments.filter(superseded=False).count(), 3)

    def test_different_tokens_allow_only_one_undo(self):
        data = self.payload(self.children[0])
        actions = [lambda: restore_personal_split(self.children[0].pk, self.coder, data),
                   lambda: restore_personal_split(self.children[0].pk, self.coder, {**data, 'token': str(uuid.uuid4())})]
        self.assertEqual(sorted(self.race(actions)), ['conflict', 'ok'])
        self.assertEqual(AuditEvent.objects.filter(action='annotation.unsplit').count(), 1)

    def test_sibling_save_and_undo_cannot_both_succeed_from_stale_preview(self):
        data = self.payload(self.children[0], confirm_coded=True)
        undo = lambda: restore_personal_split(self.children[0].pk, self.coder, data)
        save = lambda: self.save(self.children[1])
        self.assertEqual(sorted(self.race([undo, save])), ['conflict', 'ok'])

    def test_sibling_nested_split_and_undo_cannot_both_change_same_branch(self):
        data = self.payload(self.children[0], confirm_coded=True)
        undo = lambda: restore_personal_split(self.children[0].pk, self.coder, data)
        split = lambda: self.split(self.children[1], cuts=[1])
        self.assertEqual(sorted(self.race([undo, split])), ['conflict', 'ok'])
