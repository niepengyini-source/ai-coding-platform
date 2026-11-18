from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Code, Codebook, Membership, Round, SourceAnnotation, SourceCodingTask, SourceDocument
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class CodeReferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, value in fixture().items():
            setattr(cls, name, value)
        cls.url = reverse('code_references', args=[cls.project.pk, cls.book.pk, cls.first.pk])
        cls.source_round = Round.objects.create(project=cls.project, book=cls.book, kind='quick',
            started_by=cls.owner, sampling={'source_coding': True}, name='虚构文档阅读')
        cls.task = SourceCodingTask.objects.create(round=cls.source_round, document=cls.doc, coder=cls.owner)

    def setUp(self):
        self.client.force_login(self.owner)

    def test_both_sources_include_all_label_roles_and_open_the_exact_record(self):
        own = list(self.r.assignments.filter(coder=self.owner).order_by('id'))
        for record, fields in zip(own, [{'primary': self.first, 'status': 'submitted'},
                                        {'secondary': [self.second.pk, self.first.pk]}, {'labels': [self.first.pk]}]):
            self.r.assignments.filter(pk=record.pk).update(revision=1, **fields)
        selections = [SourceAnnotation.objects.create(task=self.task, start=i, end=i + 1, text='例', **fields)
                      for i, fields in enumerate([{'primary': self.first}, {'secondary': [self.first.pk]},
                                                  {'labels': [self.first.pk]}])]
        self.assertContains(self.client.get(reverse('codebook', args=[self.project.pk, self.book.pk])), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, '6 条引用')
        for record in own:
            link = reverse('history_detail', args=[self.project.pk, record.pk])
            self.assertContains(response, link)
            self.assertContains(self.client.get(link), record.unit.text)
        for selection in selections:
            self.assertContains(response, f'?task={self.task.pk}&amp;annotation={selection.pk}')
        self.assertContains(response, '草稿')
        self.assertContains(response, '已提交')
        self.assertContains(response, '已保存')

    def test_counts_and_results_are_own_only_even_for_managers_and_closed_rounds(self):
        self.r.assignments.filter(coder=self.coder).update(primary=self.first, status='submitted', revision=1)
        self.r.assignments.filter(coder=self.owner, unit=self.units[0]).update(primary=self.first, revision=0)
        self.r.assignments.filter(coder=self.owner, unit=self.units[1]).update(primary=self.first, revision=1, superseded=True)
        self.r.assignments.filter(coder=self.owner, unit=self.units[2]).update(labels=[self.first.pk * 10 + 1], revision=1)
        other_task = SourceCodingTask.objects.create(round=self.source_round, document=self.doc, coder=self.coder)
        SourceAnnotation.objects.create(task=other_task, start=0, end=3, text='他人的选段', primary=self.first)
        SourceAnnotation.objects.create(task=self.task, start=0, end=2, text='无码', primary=self.first, no_code=True)
        self.source_round.state = 'closed'
        self.source_round.save(update_fields=['state'])
        for role in ['owner', 'reviewer', 'coder', 'viewer']:
            Membership.objects.filter(project=self.project, user=self.owner).update(role=role)
            response = self.client.get(self.url, {'scope': 'all', 'page': 2})
            self.assertContains(response, '0 条引用')
            self.assertNotContains(response, '他人的选段')
        self.client.force_login(self.coder)
        self.assertContains(self.client.get(self.url), '4 条引用')

    def test_book_version_project_and_http_boundaries(self):
        newer = Codebook.objects.create(project=self.project, version=2)
        same_key = Code.objects.create(book=newer, key=self.first.key, name=self.first.name)
        self.r.assignments.filter(coder=self.owner).update(primary=self.first, revision=1)
        def url(book, code):
            return reverse('code_references', args=[self.project.pk, book.pk, code.pk])
        self.assertContains(self.client.get(url(newer, same_key)), '0 条引用')
        self.assertEqual(self.client.get(url(newer, self.first)).status_code, 404)
        self.assertEqual(self.client.get(url(self.foreign_code.book, self.foreign_code)).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertRedirects(self.client.get(self.url), '/login/?next=' + self.url, fetch_redirect_response=False)

    def test_pagination_pdf_pages_and_image_region(self):
        self.doc.normalized = {'source': {'format': 'pdf'}, 'blocks': [
            {'page': 2, 'start': 0, 'end': 60}, {'page': 3, 'start': 62, 'end': 100}]}
        self.doc.save(update_fields=['normalized'])
        for i in range(25):
            SourceAnnotation.objects.create(task=self.task, start=i, end=i + 1, text=f'虚构句{i}', primary=self.first)
        SourceAnnotation.objects.create(task=self.task, start=58, end=64, text='虚构跨页引用', primary=self.first)
        image = SourceDocument.objects.create(project=self.project, name='示例.png', normalized={'type': 'image'},
                                              raw=b'image', sha256='b' * 64, text_column='')
        image_task = SourceCodingTask.objects.create(round=self.source_round, document=image, coder=self.owner)
        SourceAnnotation.objects.create(task=image_task, start=0, end=0, text='', primary=self.first,
                                      region={'x': .2, 'y': .3, 'width': .2, 'height': .1})
        response = self.client.get(self.url)
        self.assertContains(response, '27 条引用')
        self.assertContains(response, '第 2–3 页')
        self.assertContains(response, '图片区域 · 左 20.0%')
        page2 = self.client.get(self.url, {'page': 2})
        self.assertEqual(len(page2.context['page']), 2)
        self.assertNotContains(page2, '虚构跨页引用')
