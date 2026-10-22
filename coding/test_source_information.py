"""Source summaries use fictional records and never change independent answers."""
import csv
import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import AuditEvent, Membership, SourceRecord, Unit
from .source_information import (DISPLAY_FIELDS, attach_source_information, mapping_token,
                                 suggested_display_mapping)
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class SourceInformationTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.headers = ['message_id', 'room_id', 'sender_name', 'sender_type', 'agent_role',
                        'content', 'created_at', 'source_message_id', 'source_content_preview_snapshot']
        self.document = self.units[0].record.document
        self.document.text_column = 'content'
        self.document.context_column = 'room_id'
        self.rows = [
            ['M1', 'G1', '虚构学生', 'SenderType.student', '', '这是一条问题', '2026-10-22 10:00', '', ''],
            ['M2', 'G1', '虚构助手', 'SenderType.agent', 'socratic', '这是一条回应', '2026-10-22 10:01', 'M1', '原始预览'],
        ]
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(self.headers)
        writer.writerows(self.rows)
        self.document.raw = stream.getvalue().encode('utf-8')
        self.document.save()
        self.records = []
        for position, row in enumerate(self.rows, 20):
            data = dict(zip(self.headers, row))
            text = data.pop('content')
            self.records.append(SourceRecord.objects.create(document=self.document, position=position,
                text=text, metadata=data, context_key='value:G1'))
        self.focus = self.records[1]
        self.assignment = self.r.assignments.filter(coder=self.owner).first()
        self.assignment.unit = Unit.objects.create(record=self.focus, text=self.focus.text, end=len(self.focus.text))
        self.assignment.save()
        self.url = f'/p/{self.project.pk}/materials/{self.document.pk}/display/'
        self.client.force_login(self.owner)

    def values(self, mapping=None):
        mapping = suggested_display_mapping(self.headers) if mapping is None else mapping
        return {f'source-{self.document.pk}-{key}': value for key, value in mapping.items()} | {
            'mapping_token': mapping_token(self.document), 'assignment_id': self.assignment.pk}

    def test_only_unambiguous_heading_aliases_are_suggested(self):
        mapping = suggested_display_mapping(self.headers)
        self.assertEqual(mapping['speaker'], 'sender_name')
        self.assertEqual(mapping['speaker_type'], 'sender_type')
        self.assertEqual(mapping['recipient'], '')
        self.assertEqual(suggested_display_mapping(['speaker_name', 'sender_name'])['speaker'], '')
        self.assertFalse(any(suggested_display_mapping(['任意列']).values()))

    def test_batch_summary_shows_source_identity_and_unique_reference(self):
        with self.assertNumQueries(1):
            attach_source_information(self.records, self.document)
        first, second = [record.source_information for record in self.records]
        self.assertIn({'label': '身份', 'value': '学生'}, first['items'])
        self.assertIn({'label': '身份', 'value': 'AI'}, second['items'])
        self.assertIn({'label': '角色', 'value': 'socratic'}, second['items'])
        self.assertEqual(second['reference']['text'], self.records[0].text)
        self.assertEqual(second['reference']['position'], self.records[0].position)
        self.assertNotIn('明确接收者', [item['label'] for item in second['items']])

    def test_reference_does_not_cross_source_or_conversation(self):
        self.records[0].context_key = 'different-group'
        self.records[0].save()
        SourceRecord.objects.create(document=self.foreign_code.book.project.documents.create(
            name='other.csv', raw=b'text\nx\n', sha256='source-other', text_column='text'), position=20,
            text='其他项目不可出现', metadata={'message_id': 'M1'}, context_key='value:G1')
        attach_source_information([self.focus], self.document)
        self.assertEqual(self.focus.source_information['reference']['text'], '原始预览')
        self.assertIsNone(self.focus.source_information['reference']['position'])

    def test_duplicate_message_identity_never_selects_a_reference_arbitrarily(self):
        SourceRecord.objects.create(document=self.document, position=22, text='同编号的另一条原文',
            metadata={'message_id': 'M1'}, context_key='value:G1')
        attach_source_information([self.focus], self.document)
        self.assertIn('无法唯一定位', self.focus.source_information['reference']['label'])
        self.assertEqual(self.focus.source_information['reference']['text'], '原始预览')

    def test_numeric_and_quoted_reference_identifiers_match_as_literal_text(self):
        for identifier in (123, '00123', '引号"与反斜线\\/[].$', 'true', 'null'):
            with self.subTest(identifier=identifier):
                self.records[0].metadata['message_id'] = identifier
                self.records[0].save(update_fields=['metadata'])
                self.focus.metadata['source_message_id'] = identifier
                self.focus.save(update_fields=['metadata'])
                with self.assertNumQueries(1):
                    attach_source_information([self.focus], self.document)
                self.assertEqual(self.focus.source_information['reference']['position'], self.records[0].position)
                self.assertEqual(self.focus.source_information['reference']['text'], self.records[0].text)

    def test_reference_identifier_mapped_to_text_column_uses_literal_text_matching(self):
        self.document.display_mapping = {'message_id': 'content', 'reference_id': 'source_message_id'}
        for identifier in ('123', '编号"\\[].$中文'):
            with self.subTest(identifier=identifier):
                self.records[0].text = identifier
                self.records[0].save(update_fields=['text'])
                self.focus.metadata['source_message_id'] = identifier
                self.focus.save(update_fields=['metadata'])
                with self.assertNumQueries(1):
                    attach_source_information([self.focus], self.document)
                self.assertEqual(self.focus.source_information['reference']['position'], self.records[0].position)
                self.assertEqual(self.focus.source_information['reference']['text'], identifier)

    def test_unknown_identity_is_literal_and_configured_blank_overrides_suggestions(self):
        self.focus.metadata['sender_type'] = '自定义访谈角色'
        attach_source_information([self.focus], self.document)
        self.assertIn({'label': '身份', 'value': '自定义访谈角色'}, self.focus.source_information['items'])
        self.document.display_mapping = {name: '' for name, _ in DISPLAY_FIELDS}
        with self.assertNumQueries(0):
            attach_source_information([self.focus], self.document)
        self.assertEqual(self.focus.source_information, {'items': [], 'reference': None})

    def test_custom_fields_display_without_relabeling_research_categories(self):
        self.document.display_mapping = {'speaker': '甲列', 'recipient': '乙列'}
        self.focus.metadata.update({'甲列': '虚构访谈对象', '乙列': '明确接收对象'})
        attach_source_information([self.focus], self.document)
        self.assertEqual(self.focus.source_information['items'], [
            {'label': '发言者', 'value': '虚构访谈对象'}, {'label': '明确接收者', 'value': '明确接收对象'}])

    def test_workbench_shows_info_and_get_does_not_persist_suggestions(self):
        before = list(AuditEvent.objects.values_list('pk', flat=True))
        response = self.client.get(f'/r/{self.r.pk}/?a={self.assignment.pk}')
        self.assertContains(response, '原始发言信息')
        self.assertContains(response, '虚构助手')
        self.assertContains(response, '引用消息')
        self.assertContains(response, '调整发言信息显示')
        self.document.refresh_from_db()
        self.assertEqual(self.document.display_mapping, {})
        self.assertEqual(before, list(AuditEvent.objects.values_list('pk', flat=True)))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.revision, 0)

    def test_dynamic_context_has_same_summary_and_remains_read_only(self):
        response = self.client.get(f'/api/assignments/{self.assignment.pk}/context/',
                                   {'direction': 'before', 'cursor': self.focus.position})
        first = response.json()['records'][-1]
        self.assertEqual(first['position'], self.records[0].position)
        self.assertIn({'label': '身份', 'value': '学生'}, first['source_information']['items'])
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_post_mapping_changes_only_display_and_records_audit(self):
        before = (bytes(self.document.raw), self.records[0].metadata.copy(), self.assignment.revision)
        response = self.client.post(self.url, self.values())
        self.assertRedirects(response, f'/r/{self.r.pk}/?a={self.assignment.pk}#source-information-settings', fetch_redirect_response=False)
        self.document.refresh_from_db()
        self.assertEqual(self.document.display_mapping['speaker'], 'sender_name')
        event = AuditEvent.objects.get(action='source.display_mapping')
        self.assertEqual(event.before, {})
        self.assertEqual(event.after, self.document.display_mapping)
        self.assignment.refresh_from_db()
        self.records[0].refresh_from_db()
        self.assertEqual(before, (bytes(self.document.raw), self.records[0].metadata, self.assignment.revision))

    def test_invalid_column_or_stale_token_never_changes_mapping(self):
        for values in [self.values() | {f'source-{self.document.pk}-speaker': 'not-a-column'},
                       self.values() | {'mapping_token': 'stale'}]:
            response = self.client.post(self.url, values)
            self.assertEqual(response.status_code, 302)
            self.document.refresh_from_db()
            self.assertEqual(self.document.display_mapping, {})
        self.assertFalse(AuditEvent.objects.filter(action='source.display_mapping').exists())

    def test_coder_viewer_and_outsider_cannot_change_mapping(self):
        for user in (self.coder, self.viewer, self.outsider):
            self.client.force_login(user)
            self.assertIn(self.client.post(self.url, self.values()).status_code, (403, 404))
        self.assertFalse(AuditEvent.objects.filter(action='source.display_mapping').exists())

    def test_reviewer_can_configure_and_return_never_uses_external_next(self):
        reviewer = get_user_model().objects.create_user('source_reviewer')
        Membership.objects.create(project=self.project, user=reviewer, role='reviewer')
        self.client.force_login(reviewer)
        response = self.client.post(self.url, self.values() | {'next': 'https://example.invalid',
                                                            'assignment_id': '9' * 1000})
        self.assertRedirects(response, f'/p/{self.project.pk}/materials/', fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_mapping_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.url, self.values()).status_code, 403)

    def test_import_preview_mapping_is_optional_and_does_not_import_existing_answers(self):
        raw = b'body,author,certainty,primary_code\nfictional unit,fictional speaker,yes,OLD\n'
        response = self.client.post(f'/p/{self.project.pk}/materials/', {
            'file': SimpleUploadedFile('display-fields.csv', raw, content_type='text/csv')})
        draft = response.context['draft']
        self.assertContains(response, '发言信息显示（可选）')
        response = self.client.post(f'/p/{self.project.pk}/materials/', {
            'draft_id': str(draft.pk), 'text_column': 'body', 'context_column': '',
            'source-speaker': 'author'})
        self.assertEqual(response.status_code, 302)
        document = self.project.documents.get(name='display-fields.csv')
        self.assertEqual(bytes(document.raw), raw)
        self.assertEqual(document.display_mapping['speaker'], 'author')
        self.assertEqual(document.records.get().metadata['certainty'], 'yes')
        self.assertEqual(document.records.get().metadata['primary_code'], 'OLD')
        self.assertEqual(self.r.assignments.filter(primary__isnull=False).count(), 0)

    def test_materials_existing_source_has_optional_mapping_form(self):
        response = self.client.get(f'/p/{self.project.pk}/materials/')
        self.assertContains(response, '调整发言信息显示')
        self.assertContains(response, f'action="{self.url}"')
