"""Workflow wording and navigation checks use only synthetic projects."""
from html.parser import HTMLParser

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Assignment, Decision, Membership, Round
from .tests import TEST_SETTINGS, fixture


class ActionMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.stack = []
        self.buttons = []
        self.forms = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'button':
            self.buttons.append((values, list(self.stack)))
        if tag == 'form':
            self.forms.append(values)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]


@override_settings(**TEST_SETTINGS)
class ArchiveClarityTests(TestCase):
    def setUp(self):
        cache.clear()
        for key, value in fixture().items():
            setattr(self, key, value)
        self.client.force_login(self.owner)
        self.workbench = reverse('workbench', args=[self.r.pk])
        self.manage = reverse('round_manage', args=[self.r.pk])

    def submitted_round(self):
        self.r.assignments.update(status='submitted', primary=self.first)

    def quick_round(self):
        quick = Round.objects.create(project=self.project, book=self.book, kind='quick',
                                     name='虚构个人批次', started_by=self.owner)
        Assignment.objects.create(round=quick, coder=self.owner, unit=self.units[0])
        return quick

    def test_backup_page_names_download_without_ending_work(self):
        response = self.client.get(reverse('archive', args=[self.project.pk]))
        self.assertContains(response, '<h1>项目备份与归档</h1>', html=True)
        self.assertContains(response, '下载项目备份')
        self.assertContains(response, '不结束编码，可随时下载')
        self.assertContains(response, '自定义归档导出')
        self.assertNotContains(response, '下载项目快照 ZIP')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_backup_button_still_requires_project_management_role(self):
        self.client.force_login(self.coder)
        response = self.client.get(reverse('archive', args=[self.project.pk]))
        self.assertContains(response, '自定义归档导出')
        self.assertNotContains(response, '下载项目备份')
        self.assertEqual(self.client.post(reverse('archive', args=[self.project.pk]),
                                         {'action': 'full'}).status_code, 403)

    def test_sidebar_has_consistent_backup_name(self):
        response = self.client.get(self.workbench)
        self.assertContains(response, f'href="/p/{self.project.pk}/archive/"')
        self.assertContains(response, '<span class="nav-text">项目备份与归档</span>', html=True)

    def test_unfinished_round_waits_for_submission_without_disclosing_answers(self):
        response = self.client.get(self.workbench)
        self.assertContains(response, 'id="round-actions"', count=1)
        self.assertContains(response, '全员独立提交后')
        self.assertNotContains(response, 'name="action" value="lock"')
        self.client.post(self.manage, {'action': 'lock', 'revision': self.r.revision})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_all_submitted_entry_is_visible_and_post_only(self):
        self.submitted_round()
        response = self.client.get(self.workbench)
        self.assertContains(response, '独立编码已全部提交')
        self.assertContains(response, 'id="round-actions"', count=1)
        markup = ActionMarkup(response.content.decode())
        buttons = [(attrs, parents) for attrs, parents in markup.buttons if attrs.get('value') == 'lock']
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0][0]['type'], 'submit')
        self.assertNotIn('details', buttons[0][1])
        forms = [form for form in markup.forms if form.get('action') == self.manage]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]['method'], 'post')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')
        self.assertEqual(self.client.get(self.manage).status_code, 405)
        self.client.post(self.manage, {'action': 'lock', 'revision': self.r.revision})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')

    def test_coder_cannot_enter_collaboration_for_whole_round(self):
        self.submitted_round()
        self.client.force_login(self.coder)
        self.assertNotContains(self.client.get(self.workbench), 'name="action" value="lock"')
        self.assertEqual(self.client.post(self.manage, {'action': 'lock', 'revision': self.r.revision}).status_code, 403)
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'active')

    def test_reviewer_sees_same_visible_collaboration_entry(self):
        self.submitted_round()
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        self.client.force_login(self.coder)
        response = self.client.get(self.workbench)
        self.assertContains(response, 'name="action" value="lock"', count=1)

    def test_training_does_not_claim_independent_coding(self):
        self.submitted_round()
        Round.objects.filter(pk=self.r.pk).update(kind='training')
        response = self.client.get(self.workbench)
        self.assertContains(response, '培训练习已全部提交')
        self.assertNotContains(response, '独立编码已全部提交')

    def test_review_separates_finish_and_reopen_forms(self):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        response = self.client.get(self.workbench)
        self.assertContains(response, 'id="round-actions"', count=1)
        self.assertContains(response, '>结束本轮</button>')
        self.assertContains(response, 'aria-label="重开轮次的理由" required')
        forms = [form for form in ActionMarkup(response.content.decode()).forms
                 if form.get('action') == self.manage]
        self.assertEqual(len(forms), 2)
        self.assertTrue(all(form.get('method') == 'post' for form in forms))

    def test_finish_keeps_existing_final_decision_gate(self):
        Round.objects.filter(pk=self.r.pk).update(state='review')
        self.client.post(self.manage, {'action': 'close', 'revision': self.r.revision})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')
        for unit in self.units:
            Decision.objects.create(round=self.r, unit=unit, primary=self.first,
                                    author=self.owner, note='虚构最终结论')
        self.client.post(self.manage, {'action': 'close', 'revision': self.r.revision})
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'closed')

    def test_closed_round_explains_read_only_and_links_final_export(self):
        Round.objects.filter(pk=self.r.pk).update(state='closed')
        self.client.force_login(self.coder)
        response = self.client.get(self.workbench)
        self.assertContains(response, '本轮已结束')
        self.assertContains(response, '编码记录只读')
        self.assertContains(response, f'?target=final&amp;round={self.r.pk}')
        self.assertNotContains(response, 'name="action" value="close"')

    def test_personal_finish_is_not_backup_and_requires_submission(self):
        quick = self.quick_round()
        response = self.client.get(reverse('workbench', args=[quick.pk]))
        self.assertContains(response, '<summary>结束本批编码</summary>', html=True)
        self.assertContains(response, '下载备份不需要结束编码')
        self.assertNotContains(response, '完成并归档本批')
        finish = reverse('quick_finish', args=[self.project.pk, quick.pk])
        self.client.post(finish, {'revision': quick.revision})
        quick.refresh_from_db()
        self.assertEqual(quick.state, 'active')
        quick.assignments.update(status='submitted', primary=self.first)
        self.client.post(finish, {'revision': quick.revision})
        quick.refresh_from_db()
        self.assertEqual(quick.state, 'closed')

    def test_closed_personal_batch_links_own_export_not_consensus(self):
        quick = self.quick_round()
        quick.state = 'closed'
        quick.save(update_fields=['state'])
        response = self.client.get(reverse('workbench', args=[quick.pk]))
        self.assertContains(response, '本批编码已结束')
        self.assertContains(response, f'?target=mine&amp;round={quick.pk}')
        self.assertNotContains(response, f'?target=final&amp;round={quick.pk}')
