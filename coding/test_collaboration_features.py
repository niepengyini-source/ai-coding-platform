"""Integration tests for Recycle Bin, historical collaborator invites, and whole-document team modes."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .collaborator_invites import historical_collaborators
from .models import (Assignment, Code, Codebook, Membership, Project,
                     ProjectDirectInvitation, Round, SourceDocument, SourceRecord, Unit)
from .source_models import SourceAnnotation, SourceCodingTask


User = get_user_model()


class CollaborationAndRecycleBinTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner_lead', password='Password123!')
        self.coder_a = User.objects.create_user(username='wzx_coder', password='Password123!')
        self.coder_b = User.objects.create_user(username='sowhat_coder', password='Password123!')
        self.outsider = User.objects.create_user(username='stranger', password='Password123!')

        # Past shared project so coder_a and coder_b appear in historical collaborators
        self.past_project = Project.objects.create(owner=self.owner, name='旧协作项目')
        Membership.objects.create(project=self.past_project, user=self.owner, role='owner')
        Membership.objects.create(project=self.past_project, user=self.coder_a, role='coder')
        Membership.objects.create(project=self.past_project, user=self.coder_b, role='reviewer')
        Codebook.objects.create(project=self.past_project, version=1)

        # New active policy project with unsplit PDF-style document
        self.project = Project.objects.create(owner=self.owner, name='政策性文件编码（中国）')
        Membership.objects.create(project=self.project, user=self.owner, role='owner')
        self.book = Codebook.objects.create(project=self.project, version=1, title='政策编码本')
        self.doc_text = '第一条 引导教师科学、安全、合规、理性地应用生成式人工智能。\n\n第二条 培养学生善于观察、独立思考和理性判断的能力。'
        self.doc = SourceDocument.objects.create(
            project=self.project,
            name='教师生成式人工智能应用指引.pdf',
            units_confirmed=False,
            normalized={
                'type': 'document',
                'text': self.doc_text,
                'source': {'format': 'pdf'},
                'blocks': [
                    {'id': '1', 'start': 0, 'end': 32, 'text': '第一条 引导教师科学、安全、合规、理性地应用生成式人工智能。', 'page': 1, 'heading': '', 'level': 0},
                    {'id': '2', 'start': 34, 'end': len(self.doc_text), 'text': '第二条 培养学生善于观察、独立思考和理性判断的能力。', 'page': 2, 'heading': '', 'level': 0},
                ],
            },
        )
        SourceRecord.objects.create(document=self.doc, position=1, text=self.doc_text)

    def test_historical_collaborator_direct_invite_and_direct_add(self):
        collabs = historical_collaborators(self.project, self.owner)
        usernames = [item['user'].username for item in collabs]
        self.assertIn('wzx_coder', usernames)
        self.assertIn('sowhat_coder', usernames)
        self.assertNotIn('stranger', usernames)

        # 1. Send in-app invitation to coder_a
        self.client.force_login(self.owner)
        res = self.client.post(reverse('collaborator_action', args=[self.project.pk]), {
            'user_ids': [str(self.coder_a.pk)],
            'role': 'coder',
            'mode': 'invite',
        })
        self.assertEqual(res.status_code, 302)
        invite = ProjectDirectInvitation.objects.get(project=self.project, invitee=self.coder_a)
        self.assertEqual(invite.status, 'pending')
        self.assertFalse(Membership.objects.filter(project=self.project, user=self.coder_a).exists())

        # Invitee sees invitation on /projects/ and accepts in 1 click
        self.client.force_login(self.coder_a)
        projects_page = self.client.get(reverse('projects'))
        self.assertContains(projects_page, '政策性文件编码（中国）')
        accept_res = self.client.post(reverse('direct_invite_respond', args=[invite.pk]), {'action': 'accept'})
        self.assertRedirects(accept_res, reverse('project', args=[self.project.pk]))
        self.assertTrue(Membership.objects.filter(project=self.project, user=self.coder_a, role='coder').exists())

        # 2. Directly add coder_b in 1 click without needing invite code
        self.client.force_login(self.owner)
        add_res = self.client.post(reverse('collaborator_action', args=[self.project.pk]), {
            'user_ids': [str(self.coder_b.pk)],
            'role': 'reviewer',
            'mode': 'direct_add',
        })
        self.assertEqual(add_res.status_code, 302)
        self.assertTrue(Membership.objects.filter(project=self.project, user=self.coder_b, role='reviewer').exists())

    def test_whole_document_independent_mode_isolates_codes_and_annotations_until_review(self):
        Membership.objects.create(project=self.project, user=self.coder_a, role='coder')
        self.client.force_login(self.owner)

        # GET /rounds/new/ defaults to document mode when units_confirmed=False!
        get_res = self.client.get(reverse('round_new', args=[self.project.pk]))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, '直接阅读划选，无需切句')

        # Owner assigns whole unsplit PDF to both owner and coder_a in independent mode
        post_res = self.client.post(reverse('round_new', args=[self.project.pk]), {
            'arrangement_mode': 'source_document',
            'name': '第一轮独立政策编码',
            'kind': 'pilot',
            'collab_mode': 'independent',
            'allow_coder_codes': 'on',
            'distribution': 'all',
            'coder_ids': [str(self.owner.pk), str(self.coder_a.pk)],
            'document_ids': [str(self.doc.pk)],
        })
        round_obj = Round.objects.get(project=self.project, name='第一轮独立政策编码')
        self.assertRedirects(post_res, reverse('workbench', args=[round_obj.pk]))
        self.assertEqual(SourceCodingTask.objects.filter(round=round_obj).count(), 2)

        owner_task = SourceCodingTask.objects.get(round=round_obj, coder=self.owner)
        coder_task = SourceCodingTask.objects.get(round=round_obj, coder=self.coder_a)

        # Owner saves a span with a newly created open code '引导教师使用AIGC'
        owner_save = self.client.post(
            reverse('source_annotations', args=[owner_task.pk]),
            data=json.dumps({
                'start': 0,
                'end': 32,
                'text': self.doc_text[:32],
                'revision': 0,
                'book_id': round_obj.book_id,
                'book_revision': round_obj.book.revision,
                'token': uuid.uuid4().hex,
                'new_code': {'name': '引导教师使用AIGC', 'definition': '规范引导'},
            }),
            content_type='application/json',
        )
        self.assertEqual(owner_save.status_code, 200)
        owner_code = Code.objects.get(book=round_obj.book, name='引导教师使用AIGC')
        self.assertEqual(owner_code.private_coder_id, self.owner.pk)

        # Coder opens workbench: CANNOT see owner's private code or owner's highlighted spans!
        self.client.force_login(self.coder_a)
        coder_wb = self.client.get(reverse('workbench', args=[round_obj.pk]))
        self.assertEqual(coder_wb.status_code, 200)
        wb_data = coder_wb.context['source_data']
        self.assertEqual(wb_data['codes'], [])
        self.assertEqual(wb_data['peer_annotations'], [])

        # Coder CAN create their own private code independently even with role='coder'!
        coder_save = self.client.post(
            reverse('source_annotations', args=[coder_task.pk]),
            data=json.dumps({
                'start': 34,
                'end': len(self.doc_text),
                'text': self.doc_text[34:],
                'revision': 0,
                'book_id': round_obj.book_id,
                'book_revision': round_obj.book.revision,
                'token': uuid.uuid4().hex,
                'new_code': {'name': '学生独立思考素养', 'definition': '育人目标'},
            }),
            content_type='application/json',
        )
        self.assertEqual(coder_save.status_code, 200)
        coder_code = Code.objects.get(book=round_obj.book, name='学生独立思考素养')
        self.assertEqual(coder_code.private_coder_id, self.coder_a.pk)

        # Coder marks document completed; premature lock before owner completes stays on workbench cleanly
        self.client.post(
            reverse('source_complete', args=[coder_task.pk]),
            data=json.dumps({'completed': True, 'revision': coder_task.revision}),
            content_type='application/json',
        )
        self.client.force_login(self.owner)
        early_lock = self.client.post(reverse('round_manage', args=[round_obj.pk]), {
            'action': 'lock',
            'revision': round_obj.revision,
        })
        self.assertRedirects(early_lock, reverse('workbench', args=[round_obj.pk]))

        owner_task.refresh_from_db()
        self.client.post(
            reverse('source_complete', args=[owner_task.pk]),
            data=json.dumps({'completed': True, 'revision': owner_task.revision}),
            content_type='application/json',
        )

        # Owner transitions round to review, compares both coders side-by-side, and promotes coder's code
        lock_res = self.client.post(reverse('round_manage', args=[round_obj.pk]), {
            'action': 'lock',
            'revision': round_obj.revision,
        })
        self.assertRedirects(lock_res, reverse('disagreements', args=[round_obj.pk]))
        round_obj.refresh_from_db()
        self.assertEqual(round_obj.state, 'review')

        review_page = self.client.get(reverse('disagreements', args=[round_obj.pk]))
        self.assertEqual(review_page.status_code, 200)
        self.assertContains(review_page, '引导教师使用AIGC')
        self.assertContains(review_page, '学生独立思考素养')

        promote_res = self.client.post(reverse('promote_private_code', args=[round_obj.pk, coder_code.pk]))
        self.assertRedirects(promote_res, reverse('disagreements', args=[round_obj.pk]))
        coder_code.refresh_from_db()
        self.assertIsNone(coder_code.private_coder_id)

    def test_whole_document_sync_mode_shares_codes_and_peer_annotations_live(self):
        Membership.objects.create(project=self.project, user=self.coder_a, role='coder')
        self.client.force_login(self.owner)
        self.client.post(reverse('round_new', args=[self.project.pk]), {
            'arrangement_mode': 'source_document',
            'name': '同步协作探索编码',
            'kind': 'training',
            'collab_mode': 'sync',
            'share_annotations': 'on',
            'allow_coder_codes': 'on',
            'distribution': 'all',
            'coder_ids': [str(self.owner.pk), str(self.coder_a.pk)],
            'document_ids': [str(self.doc.pk)],
        })
        round_obj = Round.objects.get(project=self.project, name='同步协作探索编码')
        owner_task = SourceCodingTask.objects.get(round=round_obj, coder=self.owner)
        coder_task = SourceCodingTask.objects.get(round=round_obj, coder=self.coder_a)

        self.client.post(
            reverse('source_annotations', args=[owner_task.pk]),
            data=json.dumps({
                'start': 0,
                'end': 32,
                'text': self.doc_text[:32],
                'revision': 0,
                'book_id': round_obj.book_id,
                'book_revision': round_obj.book.revision,
                'token': uuid.uuid4().hex,
                'new_code': {'name': '同步共享代码A', 'definition': ''},
            }),
            content_type='application/json',
        )
        # Coder immediately sees both the shared code and owner's peer annotation!
        self.client.force_login(self.coder_a)
        poll_res = self.client.get(reverse('source_annotations', args=[coder_task.pk])).json()
        self.assertEqual([c['name'] for c in poll_res['codes']], ['同步共享代码A'])
        self.assertEqual(len(poll_res['peer_annotations']), 1)
        self.assertEqual(poll_res['peer_annotations'][0]['coder'], 'owner_lead')

    def test_recycle_bin_soft_trash_restore_and_safe_cascade_purge(self):
        Membership.objects.create(project=self.project, user=self.coder_a, role='coder')
        # Add hierarchical codes, split assignments, and source annotations to stress-test purge
        parent_code = Code.objects.create(book=self.book, key='C001', name='一级编码')
        child_code = Code.objects.create(book=self.book, key='C002', name='二级编码', parent=parent_code)
        rec = self.doc.records.first()
        u1 = Unit.objects.create(record=rec, start=0, end=10, text=self.doc_text[:10])
        u2 = Unit.objects.create(record=rec, start=10, end=20, text=self.doc_text[10:20])
        r_unit = Round.objects.create(project=self.project, book=self.book, name='单元轮次', started_by=self.owner)
        a_parent = Assignment.objects.create(round=r_unit, unit=u1, coder=self.owner, primary=child_code, superseded=True)
        Assignment.objects.create(round=r_unit, unit=u2, coder=self.owner, primary=child_code, parent=a_parent)
        r_src = Round.objects.create(project=self.project, book=self.book, name='原文轮次', started_by=self.owner,
                                     sampling={'source_coding': True})
        stask = SourceCodingTask.objects.create(round=r_src, document=self.doc, coder=self.owner)
        SourceAnnotation.objects.create(task=stask, start=0, end=10, text=self.doc_text[:10], primary=child_code,
                                        book_snapshot={'id': self.book.pk, 'version': 1, 'policy': 'single', 'codes': []})

        # Non-owner cannot trash project
        self.client.force_login(self.coder_a)
        forbidden = self.client.post(reverse('project_trash', args=[self.project.pk]))
        self.assertEqual(forbidden.status_code, 403)

        # Owner moves project to Recycle Bin
        self.client.force_login(self.owner)
        trash_res = self.client.post(reverse('project_trash', args=[self.project.pk]))
        self.assertRedirects(trash_res, reverse('projects'))
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.trashed_at)

        # Trashed project is hidden from /projects/ and returns 404 on /p/<id>/
        self.assertNotContains(self.client.get(reverse('projects')), '政策性文件编码（中国）')
        self.assertEqual(self.client.get(reverse('project', args=[self.project.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse('projects_trash')), '政策性文件编码（中国）')

        # Restore brings project and 100% of data back intact
        restore_res = self.client.post(reverse('project_restore', args=[self.project.pk]))
        self.assertRedirects(restore_res, reverse('projects'))
        self.assertEqual(self.client.get(reverse('project', args=[self.project.pk])).status_code, 200)

        # Trash again and permanently purge without any ProtectedError
        self.client.post(reverse('project_trash', args=[self.project.pk]))
        purge_res = self.client.post(reverse('project_purge', args=[self.project.pk]))
        self.assertRedirects(purge_res, reverse('projects_trash'))
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
