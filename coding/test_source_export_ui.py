"""The familiar export layout reuses permission-filtered row selection."""
import csv
import hashlib
import io
import json

from django.test import TestCase, override_settings

from .models import Assignment, AuditEvent
from .tests import Helpers, TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class SourceExportUITests(Helpers, TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['speaker', 'text', 'interview'])
        for unit in cls.units:
            writer.writerow([unit.record.metadata['speaker'], unit.record.text, unit.record.metadata['interview']])
        raw = stream.getvalue().encode('utf-8-sig')
        cls.doc.raw, cls.doc.sha256 = raw, hashlib.sha256(raw).hexdigest()
        cls.doc.save(update_fields=['raw', 'sha256'])

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = f'/p/{self.project.pk}/exports/'

    def data(self, **changes):
        data = {'target': 'mine', 'round': self.r.pk, 'layout': 'source', 'format': 'source',
                'filename': '虚构原表结果', 'encoding': 'utf-8-sig', 'delimiter': ',',
                'row_match': 'quick', 'selection': 'all', 'action': 'download'}
        data.update(changes)
        return data

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response.get('Content-Disposition', ''))
        self.assertEqual(response['Cache-Control'], 'no-store')
        return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))

    def test_new_page_defaults_to_original_layout_but_does_not_write(self):
        before = AuditEvent.objects.count()
        response = self.client.get(self.url, {'target': 'mine', 'round': self.r.pk})
        self.assertEqual(response.context['export_form']['layout'].value(), 'source')
        self.assertEqual(response.context['export_form']['format'].value(), 'source')
        self.assertContains(response, '原材料＋平台结果')
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_source_download_needs_no_technical_field_configuration(self):
        a = self.own_assignment()
        self.save(a, primary=self.first.pk, note='虚构原表备注')
        before = AuditEvent.objects.count()
        rows = self.rows(self.client.post(self.url, self.data()))
        self.assertEqual(rows[0][:3], ['speaker', 'text', 'interview'])
        self.assertEqual(rows[1][:3], ['虚构参与者', self.units[0].text, 'A'])
        self.assertIn('PLAN', rows[1])
        self.assertIn('虚构原表备注', rows[1])
        self.assertEqual(len(rows), 4)
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_selected_record_and_first_n_keep_the_same_visibility_scope(self):
        ids = list(self.r.assignments.filter(coder=self.owner).values_list('pk', flat=True))
        chosen = self.own_assignment(unit=self.units[1])
        rows = self.rows(self.client.post(self.url, self.data(selection='selected', selected=[str(chosen.pk)],
            visible_ids=','.join(map(str, ids)))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], self.units[1].text)
        rows = self.rows(self.client.post(self.url, self.data(selection='first', limit=1)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], self.units[0].text)

    def test_original_layout_cannot_export_peer_answers_during_independent_phase(self):
        response = self.client.post(self.url, self.data(target='all'))
        self.assertEqual(response.status_code, 403)
        peer = self.own_assignment(self.coder)
        response = self.client.post(self.url, self.data(selection='selected', selected=[str(peer.pk)]))
        self.assertNotIn('Content-Disposition', response)
        self.assertContains(response, '部分记录已变化或不在当前范围')

    def test_broken_source_retains_form_and_legacy_export_stays_available(self):
        self.doc.raw = b'wrong\nsource\n'
        self.doc.save(update_fields=['raw'])
        response = self.client.post(self.url, self.data())
        self.assertNotIn('Content-Disposition', response)
        self.assertContains(response, '导出样式')
        self.assertIn('export_form', response.context)
        from .exporting import annotation_table
        table = annotation_table(self.r, self.r.assignments.filter(coder=self.owner))
        response = self.client.post(self.url, self.data(layout='platform', format='json', fields=table.headers))
        self.assertEqual(len(json.loads(response.content)['rows']), 3)

    def test_source_ignores_inapplicable_legacy_field_customization(self):
        response = self.client.post(self.url, self.data(fields=['not-a-field'], label_0='bad\nlabel', order_0='not-a-number'))
        self.assertEqual(self.rows(response)[0][:3], ['speaker', 'text', 'interview'])

    def test_platform_layout_rejects_auto_source_format(self):
        from .exporting import annotation_table
        table = annotation_table(self.r, self.r.assignments.filter(coder=self.owner))
        response = self.client.post(self.url, self.data(layout='platform', fields=table.headers))
        self.assertNotIn('Content-Disposition', response)
        self.assertContains(response, '请选择Excel、CSV等具体格式')
