"""Rule-update regressions use fictional data and the temporary Django test DB."""
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from .book_updates import apply_book_update, begin_update
from .models import Assignment, AuditEvent, Code, Codebook, Decision, Membership, ReliabilityReport, Round
from .services import Conflict, annotation_data, create_round
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class BookUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for key, value in fixture().items():
            setattr(cls, key, value)

    def setUp(self):
        self.book.refresh_from_db()

    def draft(self):
        return begin_update(self.project, self.owner, self.book, self.book.revision)

    def apply(self, book, selected=None, actor=None):
        return apply_book_update(self.project, actor or self.owner, book, book.revision,
                                 selected if selected is not None else {self.r.pk: self.r.revision})

    def saved(self, **values):
        entry = self.r.assignments.filter(coder=self.owner).order_by('pk').first()
        Assignment.objects.filter(pk=entry.pk).update(**{'primary': self.first, 'note': '虚构判断依据',
            'status': 'submitted', 'revision': 2, 'last_token': 'test-old-token', **values})
        entry.refresh_from_db()
        return entry

    def test_begin_copies_rules_hierarchy_and_color_without_reimport(self):
        self.second.parent = self.first
        self.second.color = '#123abc'
        self.second.save()
        book = self.draft()
        self.assertFalse(book.frozen)
        self.assertEqual((book.title, book.policy, book.version), (self.book.title, self.book.policy, 2))
        first, second = book.codes.get(key=self.first.key), book.codes.get(key=self.second.key)
        self.assertEqual(second.parent_id, first.pk)
        self.assertEqual(second.color, '#123abc')
        self.assertNotEqual(first.pk, self.first.pk)
        event = AuditEvent.objects.get(project=self.project, action='codebook.new_version')
        self.assertEqual(event.after['code_map'][str(self.first.pk)], first.pk)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)

    def test_repeated_begin_resumes_editable_copy_even_after_edits(self):
        book = self.draft()
        Code.objects.create(book=book, key='NEW', name='新代码', definition='虚构新规则')
        book.revision += 1
        book.save()
        self.assertEqual(self.draft().pk, book.pk)
        self.assertEqual(self.project.codebooks.count(), 2)
        self.assertEqual(begin_update(self.project, self.owner, book, book.revision).pk, book.pk)

    def test_begin_rejects_stale_source_and_older_unrelated_version(self):
        with self.assertRaises(Conflict):
            begin_update(self.project, self.owner, self.book, self.book.revision + 1)
        Codebook.objects.create(project=self.project, version=2, title='另一个当前编码本')
        with self.assertRaises(Conflict):
            self.draft()
        self.assertEqual(self.project.codebooks.count(), 2)

    def test_coder_viewer_and_outsider_cannot_prepare_or_apply_update(self):
        book = self.draft()
        for actor in [self.coder, self.viewer, self.outsider]:
            with self.subTest(actor=actor.username):
                with self.assertRaises(PermissionDenied):
                    begin_update(self.project, actor, book, book.revision)
                with self.assertRaises(PermissionDenied):
                    self.apply(book, actor=actor)
        self.assertEqual(self.project.codebooks.count(), 2)

    def test_reviewer_can_update_and_foreign_source_is_rejected(self):
        Membership.objects.filter(project=self.project, user=self.viewer).update(role='reviewer')
        book = begin_update(self.project, self.viewer, self.book, self.book.revision)
        self.assertEqual(self.apply(book, actor=self.viewer), 1)
        with self.assertRaises(ValueError):
            begin_update(self.project, self.owner, self.foreign_code.book, 0)

    def test_apply_maps_renamed_code_and_resets_submitted_for_review(self):
        entry = self.saved()
        updated_at = entry.updated_at
        original_unit = entry.unit_id
        original_text = entry.unit.text
        original_metadata = dict(entry.unit.record.metadata)
        book = self.draft()
        mapped = book.codes.get(key=self.first.key)
        mapped.key, mapped.name, mapped.definition = 'RENAMED', '新名称', '虚构修订后的定义'
        mapped.save()
        new_code = Code.objects.create(book=book, key='NEW', name='新增', definition='新增规则')
        self.assertEqual(self.apply(book), 1)
        entry.refresh_from_db()
        self.assertEqual((entry.primary_id, entry.status, entry.revision, entry.last_token),
                         (mapped.pk, 'draft', 3, ''))
        self.assertEqual(entry.updated_at, updated_at)
        self.assertEqual(entry.note, '虚构判断依据')
        self.assertEqual((entry.unit_id, entry.unit.text, entry.unit.record.metadata),
                         (original_unit, original_text, original_metadata))
        self.assertNotEqual(entry.primary_id, new_code.pk)
        self.r.refresh_from_db()
        self.assertEqual((self.r.book_id, self.r.cycle, self.r.revision), (book.pk, 2, 1))
        self.assertTrue(self.r.sampling['rules_updated'])
        book.refresh_from_db()
        self.assertTrue(book.frozen)
        self.first.refresh_from_db()
        self.assertEqual(self.first.key, 'PLAN')

    def test_unsaved_records_stay_unstarted_and_saved_drafts_keep_flags(self):
        entry = self.r.assignments.filter(coder=self.owner).first()
        Assignment.objects.filter(pk=entry.pk).update(revision=1, uncertain=True, no_code=True,
                                                     note='虚构待核对原因', last_token='old-token')
        untouched = self.r.assignments.exclude(pk=entry.pk).first()
        book = self.draft()
        self.apply(book)
        entry.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual((entry.revision, entry.uncertain, entry.no_code, entry.note),
                         (2, True, True, '虚构待核对原因'))
        self.assertEqual((untouched.revision, untouched.status, untouched.last_token), (0, 'draft', ''))

    def test_secondary_and_superseded_parent_ids_are_mapped(self):
        self.book.policy = 'multi'
        self.book.save()
        entry = self.saved(secondary=[self.second.pk], superseded=True)
        book = self.draft()
        self.apply(book)
        entry.refresh_from_db()
        self.assertEqual(entry.primary_id, book.codes.get(key=self.first.key).pk)
        self.assertEqual(entry.secondary, [book.codes.get(key=self.second.key).pk])
        self.assertTrue(entry.superseded)
        self.assertEqual(entry.status, 'submitted')

    def test_mapping_composes_explicit_clone_chain_without_matching_keys(self):
        entry = self.saved()
        first_draft = self.draft()
        code = first_draft.codes.get(key=self.first.key)
        code.key = 'FIRST_RENAME'
        code.save()
        first_draft.frozen = True
        first_draft.save()
        target = begin_update(self.project, self.owner, first_draft, first_draft.revision)
        target_code = target.codes.get(key='FIRST_RENAME')
        target_code.key = 'SECOND_RENAME'
        target_code.save()
        self.apply(target)
        entry.refresh_from_db()
        self.assertEqual(entry.primary_id, target_code.pk)

    def test_deleted_used_code_rejects_every_selected_batch_without_mutation(self):
        entry = self.saved(primary=self.second)
        second_round = create_round(self.project, self.owner, self.book.pk, '虚构另一个批次',
                                    'formal', [self.owner.pk], 0, 2026, 'all', 0)
        Assignment.objects.filter(round=second_round).update(primary=self.first, revision=1)
        self.book.refresh_from_db()
        book = self.draft()
        book.codes.get(key=self.first.key).delete()
        before = annotation_data(entry)
        with self.assertRaisesMessage(ValueError, '已删除或无法对应'):
            self.apply(book, {self.r.pk: self.r.revision, second_round.pk: second_round.revision})
        for r in [self.r, second_round]:
            r.refresh_from_db()
            self.assertEqual((r.book_id, r.cycle, r.revision), (self.book.pk, 1, 0))
        entry.refresh_from_db()
        self.assertEqual(annotation_data(entry), before)
        book.refresh_from_db()
        self.assertFalse(book.frozen)
        self.assertFalse(AuditEvent.objects.filter(action='round.book_update').exists())

    def test_deleted_unused_code_does_not_prevent_apply(self):
        entry = self.saved()
        book = self.draft()
        book.codes.get(key=self.second.key).delete()
        self.assertEqual(self.apply(book), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.primary_id, book.codes.get(key=self.first.key).pk)

    def test_single_policy_with_existing_secondary_is_not_silently_cleared(self):
        self.book.policy = 'multi'
        self.book.save()
        entry = self.saved(secondary=[self.second.pk])
        book = self.draft()
        book.policy = 'single'
        book.save()
        with self.assertRaisesMessage(ValueError, '次代码'):
            self.apply(book)
        entry.refresh_from_db()
        self.assertEqual(entry.secondary, [self.second.pk])

    def test_stale_book_and_round_revisions_are_rejected_atomically(self):
        book = self.draft()
        with self.assertRaises(Conflict):
            apply_book_update(self.project, self.owner, book, book.revision + 1, {self.r.pk: self.r.revision})
        with self.assertRaises(Conflict):
            self.apply(book, {self.r.pk: self.r.revision + 1})
        self.r.refresh_from_db()
        self.assertEqual((self.r.book_id, self.r.cycle), (self.book.pk, 1))

    def test_review_and_closed_rounds_remain_on_original_book(self):
        book = self.draft()
        for state in ['review', 'closed']:
            with self.subTest(state=state):
                self.r.state = state
                self.r.save()
                with self.assertRaisesMessage(ValueError, '只能更新进行中'):
                    self.apply(book)
                self.r.refresh_from_db()
                self.assertEqual(self.r.book_id, self.book.pk)

    def test_foreign_unknown_empty_and_invalid_round_selection_is_rejected(self):
        book = self.draft()
        foreign = Round.objects.create(project=self.other, book=self.foreign_code.book,
                                        name='虚构私有批次', kind='quick', started_by=self.outsider)
        for selected in [{}, {foreign.pk: 0}, {99999999: 0}, {True: 0}, {-1: 0}, {2 ** 64: 0}, {'1': 0}]:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.apply(book, selected)
        foreign.refresh_from_db()
        self.assertEqual(foreign.book_id, self.foreign_code.book_id)

    def test_legacy_unmapped_or_unrelated_version_is_not_matched_by_key(self):
        entry = self.saved()
        book = self.draft()
        event = AuditEvent.objects.get(project=self.project, action='codebook.new_version')
        event.after.pop('code_map')
        event.save()
        with self.assertRaisesMessage(ValueError, '对应关系'):
            self.apply(book)
        entry.refresh_from_db()
        self.assertEqual(entry.primary_id, self.first.pk)
        self.assertEqual(entry.status, 'submitted')

    def test_empty_target_cannot_replace_usable_rules(self):
        book = self.draft()
        book.codes.all().delete()
        with self.assertRaisesMessage(ValueError, '至少一个代码'):
            self.apply(book)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)

    def test_only_selected_active_round_changes(self):
        other_round = create_round(self.project, self.owner, self.book.pk, '保留原规则的虚构批次',
                                   'formal', [self.owner.pk], 0, 2026, 'all', 0)
        self.book.refresh_from_db()
        book = self.draft()
        self.apply(book)
        other_round.refresh_from_db()
        self.assertEqual((other_round.book_id, other_round.cycle, other_round.revision), (self.book.pk, 1, 0))

    def test_unrelated_current_book_has_no_guessed_code_lineage(self):
        self.saved()
        book = Codebook.objects.create(project=self.project, version=2)
        Code.objects.create(book=book, key=self.first.key, name=self.first.name, definition=self.first.definition)
        with self.assertRaisesMessage(ValueError, '对应关系'):
            self.apply(book)

    def test_stale_existing_target_and_foreign_target_are_not_applied(self):
        book = self.draft()
        Codebook.objects.create(project=self.project, version=3, title='更晚的编码本')
        for target in [book, self.foreign_code.book]:
            with self.subTest(target=target.pk), self.assertRaises(Conflict):
                self.apply(target)
        self.r.refresh_from_db()
        self.assertEqual(self.r.book_id, self.book.pk)

    def test_audit_keeps_complete_before_and_after_labels_and_original_codes(self):
        entry = self.saved()
        book = self.draft()
        self.apply(book)
        event = AuditEvent.objects.get(project=self.project, action='round.book_update')
        before = next(row for row in event.before['assignments'] if row['id'] == entry.pk)
        after = next(row for row in event.after['assignments'] if row['id'] == entry.pk)
        self.assertEqual((event.before['book_id'], event.after['book_id']), (self.book.pk, book.pk))
        self.assertEqual((before['primary'], before['status'], before['revision']), (self.first.pk, 'submitted', 2))
        self.assertEqual(after['primary'], book.codes.get(key=self.first.key).pk)
        self.assertEqual(after['status'], 'draft')
        self.assertEqual(before['note'], after['note'])
        self.assertTrue(Code.objects.filter(pk=self.first.pk, book=self.book).exists())

    def test_historical_decisions_and_reports_are_preserved_but_not_current_cycle(self):
        entry = self.saved()
        decision = Decision.objects.create(round=self.r, unit=entry.unit, primary=self.first,
            author=self.owner, note='虚构旧协商结果', cycle=self.r.cycle)
        report = ReliabilityReport.objects.create(round=self.r, cycle=self.r.cycle, author=self.owner,
            data={'book_version': self.book.version, 'snapshot': [{'primary': self.first.pk}]})
        book = self.draft()
        self.apply(book)
        decision.refresh_from_db()
        report.refresh_from_db()
        self.r.refresh_from_db()
        self.assertEqual((decision.primary_id, decision.cycle), (self.first.pk, 1))
        self.assertEqual(report.data['snapshot'][0]['primary'], self.first.pk)
        self.assertFalse(self.r.decisions.filter(cycle=self.r.cycle).exists())
        self.assertFalse(self.r.reports.filter(cycle=self.r.cycle).exists())

    def test_already_current_selection_is_safe_and_no_repeated_cycle_increment(self):
        book = self.draft()
        self.apply(book)
        self.r.refresh_from_db()
        book.refresh_from_db()
        with self.assertRaisesMessage(ValueError, '无需更新'):
            self.apply(book)
        self.r.refresh_from_db()
        self.assertEqual(self.r.cycle, 2)
        self.assertEqual(AuditEvent.objects.filter(action='round.book_update').count(), 1)
