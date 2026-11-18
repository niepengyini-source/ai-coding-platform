"""Fictional image upload and annotation through the public workbench routes."""
import io
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .material_normalization import normalize_material
from .models import Code, Codebook, Membership, Project
from .tests import TEST_SETTINGS


@override_settings(**TEST_SETTINGS)
class ImageCodingTests(TestCase):
    def test_image_only_project_can_upload_save_regions_and_reopen_them(self):
        owner = get_user_model().objects.create_user('image-owner')
        project = Project.objects.create(owner=owner, name='虚构图片编码')
        Membership.objects.create(project=project, user=owner, role='owner')
        book = Codebook.objects.create(project=project, version=1, policy='single')
        code = Code.objects.create(book=book, key='EX', name='流程节点')
        self.client.force_login(owner)
        stream = io.BytesIO()
        Image.new('RGB', (12, 8), 'navy').save(stream, 'PNG')
        raw = stream.getvalue()
        materials = reverse('materials', args=[project.pk])
        upload = self.client.post(materials, {'file': SimpleUploadedFile('flow.png', raw)})
        self.assertEqual(upload.context['document_preview']['image'], {'width': 12, 'height': 8})
        response = self.client.post(materials, {'draft_id': upload.context['draft'].pk, 'text_column': 'text'})
        document = project.documents.get()
        start = reverse('source_start', args=[project.pk])
        self.assertRedirects(response, f'{start}?document={document.pk}', fetch_redirect_response=False)
        response = self.client.post(start, {'document_id': document.pk}, follow=True)
        self.assertContains(response, '选择整张图片')
        self.assertContains(response, '按坐标框选')
        self.assertEqual(bytes(document.raw), raw)
        self.assertFalse(document.records.get().units.exists())
        preview = self.client.get(reverse('source_preview', args=[project.pk, document.pk]))
        self.assertEqual(preview['Content-Type'], 'image/png')
        self.assertEqual(preview.content, raw)
        task = document.coding_tasks.get()
        annotations = reverse('source_annotations', args=[task.pk])
        payload = {'book_id': book.pk, 'primary': code.pk, 'token': uuid.uuid4().hex, 'revision': 0}
        region = {'x': .1, 'y': .2, 'width': .3, 'height': .4}
        partial = self.client.post(annotations, {**payload, 'region': region}, content_type='application/json')
        self.assertEqual(partial.status_code, 200, partial.content)
        annotation = partial.json()['annotation']
        self.assertEqual(annotation['region'], region)
        self.assertEqual(annotation['book_snapshot']['source_file_sha256'], document.sha256)
        full = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
        response = self.client.post(annotations, {**payload, 'region': full}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(task.annotations.count(), 2)
        retry = self.client.post(annotations, {**payload, 'region': region}, content_type='application/json')
        self.assertEqual(retry.json()['annotation']['id'], annotation['id'])
        response = self.client.post(annotations, {**payload, 'region': {**region, 'x': .8}}, content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        detail = self.client.get(annotations, {'id': annotation['id']})
        self.assertEqual(detail.json()['annotation']['region'], region)
        self.assertEqual(task.annotations.count(), 2)

    def test_image_type_verified_before_accepting(self):
        stream = io.BytesIO()
        Image.new('RGB', (12, 8), 'navy').save(stream, 'JPEG')
        raw = stream.getvalue()
        self.assertEqual(normalize_material('poster.jpeg', raw)['source']['format'], 'jpeg')
        with self.assertRaisesMessage(ValueError, '图片无法读取'):
            normalize_material('poster.png', raw)
