"""Synthetic long-document preparation; no real policies or research database."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from .document_units import draft_ranges, update_document_units
from .models import (Assignment, AuditEvent, Code, Codebook, Membership, Project,
                     Round, SourceDocument, SourceRecord, Unit)
from .services import Conflict


class DocumentPreparationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('document_prepare_owner')
        cls.coder = get_user_model().objects.create_user('document_prepare_coder')
        cls.outsider = get_user_model().objects.create_user('document_prepare_outsider')
        cls.project = Project.objects.create(name='虚构政策整理', owner=cls.owner)
        Membership.objects.create(project=cls.project, user=cls.owner, role='owner')
        Membership.objects.create(project=cls.project, user=cls.coder, role='coder')

    def setUp(self):
        self.text = '第一节 总则\n\n学校应核对😀生成内容。\n\n教师应记录使用依据。'
        self.first_start = self.text.index('学校')
        self.second_start = self.text.index('教师')
        self.doc = SourceDocument.objects.create(project=self.project, name='synthetic.pdf',
            sha256='0' * 64, raw=b'fiction', text_column='text', units_confirmed=False,
            normalized={'schema_version': 1, 'type': 'document', 'text': self.text, 'blocks': [
                {'id': 'b1', 'start': 0, 'end': len('第一节 总则'), 'text': '第一节 总则', 'level': 1, 'page': 1},
                {'id': 'b2', 'start': self.first_start, 'end': self.second_start - 2,
                 'text': self.text[self.first_start:self.second_start - 2], 'page': 1},
                {'id': 'b3', 'start': self.second_start, 'end': len(self.text),
                 'text': self.text[self.second_start:], 'page': 2},
            ]})
        self.record = SourceRecord.objects.create(document=self.doc, position=1, text=self.text)

    def change(self, action, **values):
        self.doc.refresh_from_db()
        self.doc = update_document_units(self.doc.pk, self.owner,
            {'action': action, 'revision': str(self.doc.unit_revision), **values})
        return self.doc

    def add(self, start, end):
        return self.change('add', start=str(start), end=str(end), text=self.text[start:end])

    def test_cross_paragraph_unicode_selection_creates_only_candidate_until_confirmation(self):
        self.add(self.first_start, len(self.text))
        self.assertEqual(draft_ranges(self.doc), [[self.first_start, len(self.text)]])
        self.assertFalse(self.doc.units_confirmed)
        self.assertFalse(Unit.objects.exists())
        self.change('confirm')
        self.assertTrue(self.doc.units_confirmed)
        unit = Unit.objects.get(record=self.record)
        self.assertEqual(unit.text, self.text[self.first_start:])
        self.assertEqual((unit.start, unit.end), (self.first_start, len(self.text)))
        self.record.refresh_from_db()
        self.assertEqual(self.record.text, self.text)

    def test_browser_form_crlf_is_matched_to_canonical_lf_without_moving_offsets(self):
        self.change('add', start=str(self.first_start), end=str(len(self.text)),
                    text=self.text[self.first_start:].replace('\n', '\r\n'))
        self.assertEqual(draft_ranges(self.doc), [[self.first_start, len(self.text)]])

    def test_positions_and_selected_text_must_match_and_duplicate_range_is_rejected(self):
        for values in ({'start': '-1', 'end': '4', 'text': 'bad'},
                       {'start': '0', 'end': str(len(self.text) + 1), 'text': self.text},
                       {'start': '0', 'end': '3', 'text': '不匹配'}):
            with self.assertRaises(ValueError):
                self.change('add', **values)
        self.add(self.first_start, self.second_start - 2)
        with self.assertRaisesMessage(ValueError, '已在候选列表'):
            self.add(self.first_start, self.second_start - 2)
        self.assertEqual(len(draft_ranges(self.doc)), 1)

    def test_paragraph_suggestions_skip_headings_and_can_be_removed_and_undone(self):
        self.change('suggest')
        self.assertEqual(draft_ranges(self.doc), [[self.first_start, self.second_start - 2],
                                                 [self.second_start, len(self.text)]])
        self.change('remove', index='0')
        self.assertEqual(draft_ranges(self.doc), [[self.second_start, len(self.text)]])
        self.change('undo')
        self.assertEqual(len(draft_ranges(self.doc)), 2)
        self.assertFalse(self.doc.units_confirmed)
        self.assertFalse(Unit.objects.exists())

    def test_confirmation_keeps_old_assigned_unit_and_reuses_unchanged_ranges(self):
        self.add(self.first_start, self.second_start - 2)
        self.add(self.second_start, len(self.text))
        self.change('confirm')
        original = list(Unit.objects.filter(record=self.record).order_by('start'))
        book = Codebook.objects.create(project=self.project, version=1)
        code = Code.objects.create(book=book, key='A', name='依据', definition='虚构规则')
        round_ = Round.objects.create(project=self.project, book=book, name='原轮次', kind='formal')
        assignment = Assignment.objects.create(round=round_, unit=original[0], coder=self.coder,
                                               status='submitted', primary=code, note='旧判断')
        self.change('remove', index='0')
        self.add(self.first_start, len(self.text))
        self.change('confirm')
        assignment.refresh_from_db()
        original[0].refresh_from_db()
        original[1].refresh_from_db()
        self.assertEqual(assignment.unit_id, original[0].pk)
        self.assertEqual(assignment.note, '旧判断')
        self.assertEqual(assignment.unit.text, self.text[self.first_start:self.second_start - 2])
        self.assertFalse(original[0].active)
        self.assertTrue(original[1].active)
        self.assertEqual(Unit.objects.filter(record=self.record).count(), 3)

    def test_undo_returns_to_published_ranges_without_changing_old_units(self):
        self.add(self.first_start, self.second_start - 2)
        self.change('confirm')
        unit = Unit.objects.get(record=self.record)
        self.change('remove', index='0')
        self.assertFalse(self.doc.units_confirmed)
        self.change('undo')
        self.assertTrue(self.doc.units_confirmed)
        self.assertEqual(list(Unit.objects.values_list('pk', flat=True)), [unit.pk])

    def test_stale_revision_and_unauthorized_editor_do_not_change_ranges(self):
        self.add(self.first_start, self.second_start - 2)
        with self.assertRaises(Conflict):
            update_document_units(self.doc.pk, self.owner, {'action': 'suggest', 'revision': '0'})
        for actor in (self.coder, self.outsider):
            with self.assertRaises(PermissionDenied):
                update_document_units(self.doc.pk, actor,
                    {'action': 'remove', 'revision': str(self.doc.unit_revision), 'index': '0'})
        self.doc.refresh_from_db()
        self.assertEqual(len(draft_ranges(self.doc)), 1)

    def test_empty_confirmation_rejected_and_confirmation_writes_audit(self):
        with self.assertRaisesMessage(ValueError, '至少一个'):
            self.change('confirm')
        self.add(self.first_start, len(self.text))
        self.change('confirm')
        self.assertTrue(AuditEvent.objects.filter(project=self.project, action='document.units.confirm',
                                                  after__confirmed=True).exists())

    def test_page_only_pdf_does_not_pretend_pages_are_paragraphs(self):
        self.doc.normalized['source'] = {'format': 'pdf'}
        self.doc.normalized['blocks'] = [
            {'id': 'page1', 'start': 0, 'end': len(self.text), 'text': self.text, 'page': 1}]
        self.doc.save(update_fields=['normalized'])
        with self.assertRaisesMessage(ValueError, '按页提取'):
            self.change('suggest')
        self.assertFalse(Unit.objects.exists())

    def test_reading_chunks_are_bounded_without_changing_global_unicode_positions(self):
        from .document_views import reading_blocks
        text = '😀' * 10001
        blocks = reading_blocks([{'id': 'huge', 'start': 10, 'end': 10011, 'text': text, 'level': 1}])
        self.assertEqual([len(b['text']) for b in blocks], [4000, 4000, 2001])
        self.assertEqual([(b['start'], b['end']) for b in blocks], [(10, 4010), (4010, 8010), (8010, 10011)])
        self.assertEqual(''.join(b['text'] for b in blocks), text)
        self.assertEqual([b['level'] for b in blocks], [1, 0, 0])

    def test_overlapping_highlights_preserve_text_once_and_clip_to_visible_block(self):
        from .document_views import _pieces
        pieces = _pieces('0123456789', 2, 8, [[0, 4], [3, 6], [7, 10]])
        self.assertEqual(pieces, [{'text': '2345', 'selected': True},
                                  {'text': '6', 'selected': False}, {'text': '7', 'selected': True}])
