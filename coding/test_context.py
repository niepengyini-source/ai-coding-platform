"""Context navigation never crosses a source conversation or exposes another coder."""
from django.test import TestCase, override_settings

from .context import context_page
from .models import Assignment, SourceRecord, Unit
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class ContextTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.coder)
        self.document = self.units[0].record.document
        self.rows = [SourceRecord.objects.create(document=self.document, position=i,
            text=f'上下文第{i}条', context_key='context-test') for i in range(10, 25)]
        self.focus = self.rows[7]
        unit = Unit.objects.create(record=self.focus, end=len(self.focus.text), text=self.focus.text)
        self.a = Assignment.objects.create(round=self.r, unit=unit, coder=self.coder)
        self.url = f'/api/assignments/{self.a.pk}/context/'
        SourceRecord.objects.create(document=self.document, position=25, text='另一个小组', context_key='another-group')

    def test_workbench_defaults_to_two_before_and_two_after(self):
        response = self.client.get(f'/r/{self.r.pk}/?a={self.a.pk}')
        self.assertEqual([row.position for row in response.context['context']], [15, 16, 17, 18, 19])
        self.assertContains(response, '↑ 查看上文 +3')
        self.assertContains(response, '↓ 查看下文 +3')
        self.assertContains(response, 'class="annotation-actions"')
        html = response.content.decode()
        self.assertLess(html.index('data-action="submit_next"'), html.index('data-primary-fallback'))

    def test_more_before_and_after_is_three_at_a_time_in_original_order(self):
        before = self.client.get(self.url, {'direction': 'before', 'cursor': 15}).json()
        self.assertEqual([row['position'] for row in before['records']], [12, 13, 14])
        self.assertEqual(before['cursor'], 12)
        self.assertTrue(before['has_more'])
        after = self.client.get(self.url, {'direction': 'after', 'cursor': 19}).json()
        self.assertEqual([row['position'] for row in after['records']], [20, 21, 22])
        self.assertEqual(after['cursor'], 22)
        self.assertTrue(after['has_more'])
        last = self.client.get(self.url, {'direction': 'after', 'cursor': 22})
        self.assertEqual([row['position'] for row in last.json()['records']], [23, 24])
        self.assertFalse(last.json()['has_more'])
        self.assertEqual(last['Cache-Control'], 'no-store')

    def test_beginning_end_and_empty_page(self):
        first = self.client.get(self.url, {'direction': 'before', 'cursor': 12}).json()
        self.assertEqual([row['position'] for row in first['records']], [10, 11])
        self.assertFalse(first['has_more'])
        empty = self.client.get(self.url, {'direction': 'before', 'cursor': 10}).json()
        self.assertEqual(empty['records'], [])
        self.assertEqual(empty['cursor'], 10)

    def test_context_stays_within_same_document_and_key(self):
        self.assertEqual([row.position for row in context_page(self.focus, 'after', size=50)[0]], list(range(18, 25)))
        SourceRecord.objects.create(document=self.foreign_code.book.project.documents.create(
            name='foreign.csv', raw=b'x', sha256='foreign-context', text_column='text'),
            position=18, text='其他项目', context_key='context-test')
        items, _, _ = context_page(self.focus, 'after', size=50)
        self.assertNotIn('其他项目', [row.text for row in items])

    def test_bad_cursor_or_direction_is_rejected(self):
        for query in [{'direction': 'left', 'cursor': '15'}, {'direction': 'before', 'cursor': '18'},
                      {'direction': 'after', 'cursor': '16'}, {'direction': 'before', 'cursor': '-1'},
                      {'direction': 'before', 'cursor': '1.1'}, {'direction': 'before', 'cursor': ''},
                      {'direction': 'before', 'cursor': '9' * 20}]:
            self.assertEqual(self.client.get(self.url, query).status_code, 400)

    def test_only_own_assignment_and_member_can_request_context(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url, {'direction': 'before', 'cursor': 15}).status_code, 404)
        self.client.force_login(self.coder)
        self.a.superseded = True
        self.a.save()
        self.assertEqual(self.client.get(self.url, {'direction': 'before', 'cursor': 15}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_context_endpoint_is_read_only(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.a.refresh_from_db()
        self.assertEqual(self.a.revision, 0)
