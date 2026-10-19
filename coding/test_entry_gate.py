"""Start buttons route members to the missing project resource, without mutations."""
import re
import uuid

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent, Code, Codebook, Membership, Project, Round, Unit
from .tests import TEST_SETTINGS, fixture


@override_settings(**TEST_SETTINGS)
class CodingEntryGateTests(TestCase):
    def setUp(self):
        for name, value in fixture().items():
            setattr(self, name, value)
        self.client.force_login(self.owner)
        self.entry = reverse('coding_entry', args=[self.project.pk])
        self.quick = reverse('quick_start', args=[self.project.pk])

    def missing_units(self):
        Unit.objects.filter(record__document__project=self.project).update(active=False)

    def empty_current_book(self):
        return Codebook.objects.create(project=self.project, version=2, title='待补充的当前规则')

    def assert_gate(self, url, destination, message, method='get'):
        count = Round.objects.count()
        audit_count = AuditEvent.objects.count()
        response = getattr(self.client, method)(url, {'book_id': self.book.pk, 'token': uuid.uuid4()})
        self.assertRedirects(response, destination, fetch_redirect_response=False)
        self.assertTrue(any(message in str(item) for item in get_messages(response.wsgi_request)))
        self.assertEqual(Round.objects.count(), count)
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        return response

    def test_both_missing_prioritizes_materials_even_with_existing_round(self):
        self.missing_units()
        self.empty_current_book()
        self.assert_gate(self.entry, reverse('materials', args=[self.project.pk]), '尚未导入有效编码单元')

    def test_missing_units_with_ready_rules_routes_to_material_import(self):
        self.missing_units()
        self.assert_gate(self.entry, reverse('materials', args=[self.project.pk]), '请先导入编码材料')

    def test_missing_rules_routes_to_current_book_not_historical_ready_book(self):
        current = self.empty_current_book()
        self.assert_gate(self.entry, reverse('codebook', args=[self.project.pk, current.pk]), '请先导入编码本或添加代码')

    def test_ready_resources_continue_existing_assigned_round(self):
        self.assertRedirects(self.client.get(self.entry), reverse('workbench', args=[self.r.pk]))

    def test_ready_resources_without_open_round_go_to_quick_start(self):
        Round.objects.filter(project=self.project).update(state='closed')
        response = self.client.get(self.entry)
        self.assertRedirects(response, self.quick)
        self.assertContains(self.client.get(self.quick), '确认编码准备')

    def test_ready_editable_current_book_is_accepted_without_manual_freeze(self):
        current = self.empty_current_book()
        Code.objects.create(book=current, key='NEW', name='新规则', definition='虚构测试规则')
        response = self.client.get(self.quick)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_book'], current)
        current.refresh_from_db()
        self.assertFalse(current.frozen)

    def test_quick_start_get_cannot_bypass_missing_material_gate(self):
        self.missing_units()
        self.assert_gate(self.quick, reverse('materials', args=[self.project.pk]), '尚未导入有效编码单元')

    def test_quick_start_post_cannot_bypass_missing_material_gate(self):
        self.missing_units()
        self.assert_gate(self.quick, reverse('materials', args=[self.project.pk]), '尚未导入有效编码单元', method='post')

    def test_quick_start_post_cannot_use_old_book_when_current_is_empty(self):
        current = self.empty_current_book()
        self.assert_gate(self.quick, reverse('codebook', args=[self.project.pk, current.pk]), '没有可用的编码规则', method='post')

    def test_coder_missing_material_goes_to_accessible_units_page(self):
        self.client.force_login(self.coder)
        self.missing_units()
        response = self.assert_gate(self.entry, reverse('units', args=[self.project.pk]), '请联系负责人导入编码材料')
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_coder_missing_rules_goes_to_accessible_current_rule_page(self):
        self.client.force_login(self.coder)
        current = self.empty_current_book()
        response = self.assert_gate(self.entry, reverse('codebook', args=[self.project.pk, current.pk]), '请联系负责人导入编码本')
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_reviewer_missing_material_goes_to_import_page(self):
        Membership.objects.filter(project=self.project, user=self.coder).update(role='reviewer')
        self.client.force_login(self.coder)
        self.missing_units()
        response = self.assert_gate(self.entry, reverse('materials', args=[self.project.pk]), '请先导入编码材料')
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_viewer_and_non_member_receive_no_resource_information(self):
        self.missing_units()
        for user, status in [(self.viewer, 403), (self.outsider, 404)]:
            self.client.force_login(user)
            for url in [self.entry, self.quick]:
                with self.subTest(user=user.username, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, status)
                    self.assertNotContains(response, '尚未导入有效编码单元', status_code=status)

    def test_abnormal_project_without_codebook_uses_safe_read_only_fallback(self):
        project = Project.objects.create(name='无编码本的虚构项目', owner=self.owner)
        Membership.objects.create(project=project, user=self.owner, role='owner')
        # A material in another project must not satisfy this project's requirements.
        entry = reverse('coding_entry', args=[project.pk])
        self.assert_gate(entry, reverse('materials', args=[project.pk]), '尚未导入有效编码单元')
        self.doc.project = project
        self.doc.save(update_fields=['project'])
        count = Round.objects.count()
        response = self.client.get(entry)
        self.assertContains(response, '请联系平台维护者恢复')
        self.assertContains(response, '编码本暂不可用')
        self.assertNotContains(response, '开始 / 继续我的编码')
        self.assertTemplateUsed(response, 'coding/quick_start.html')
        self.assertEqual(Round.objects.count(), count)
        self.assertFalse(project.codebooks.exists())

    def test_start_buttons_remain_available_before_and_after_resources_are_ready(self):
        urls = [reverse('project', args=[self.project.pk]), reverse('materials', args=[self.project.pk]),
                reverse('units', args=[self.project.pk]), reverse('codebook', args=[self.project.pk, self.book.pk])]
        for ready in [True, False]:
            if not ready:
                self.missing_units()
                self.empty_current_book()
            for url in urls:
                with self.subTest(ready=ready, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f'href="{self.entry}"')
                    self.assertNotContains(response, f'disabled href="{self.entry}"')

    def test_read_only_members_have_no_start_buttons(self):
        self.client.force_login(self.viewer)
        for name, args in [('project', [self.project.pk]), ('units', [self.project.pk]),
                           ('codebook', [self.project.pk, self.book.pk])]:
            with self.subTest(page=name):
                self.assertNotContains(self.client.get(reverse(name, args=args)), f'href="{self.entry}"')

    def setup_notice(self, response):
        return re.search(r'<div class="notice[^"]*" role="status">(.*?)</div>',
                         response.content.decode(), re.S).group(1)

    def test_sidebar_start_uses_same_checked_entry_on_every_project_page(self):
        for name, args in [('project', [self.project.pk]), ('materials', [self.project.pk]),
                           ('units', [self.project.pk]), ('codebook', [self.project.pk, self.book.pk])]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                sidebar = response.content.decode().split('<nav aria-label="主要导航">')[1].split('</nav>')[0]
                self.assertIn(f'href="{self.entry}">▶ 开始 / 继续编码', sidebar)

    def test_missing_material_notice_has_direct_upload_action_for_managers(self):
        self.missing_units()
        for role in ['owner', 'reviewer']:
            Membership.objects.filter(project=self.project, user=self.owner).update(role=role)
            with self.subTest(role=role):
                response = self.client.get(self.entry, follow=True)
                self.assertEqual(response.redirect_chain, [(reverse('materials', args=[self.project.pk]), 302)])
                self.assertIn(f'href="/p/{self.project.pk}/materials/#material-upload">导入编码材料',
                              self.setup_notice(response))
                self.assertContains(response, 'id="material-upload"')

    def test_missing_rules_notice_links_current_book_import_not_historical_book(self):
        current = self.empty_current_book()
        for role in ['owner', 'reviewer']:
            Membership.objects.filter(project=self.project, user=self.owner).update(role=role)
            with self.subTest(role=role):
                response = self.client.get(self.entry, follow=True)
                notice = self.setup_notice(response)
                self.assertIn(f'href="/p/{self.project.pk}/books/{current.pk}/import/">导入编码本', notice)
                self.assertNotIn(f'/books/{self.book.pk}/import/', notice)

    def test_both_missing_notice_prioritizes_material_upload_action(self):
        self.missing_units()
        current = self.empty_current_book()
        response = self.client.get(self.entry, follow=True)
        notice = self.setup_notice(response)
        self.assertIn(f'/p/{self.project.pk}/materials/#material-upload', notice)
        self.assertNotIn(f'/books/{current.pk}/import/', notice)

    def test_coder_setup_actions_do_not_offer_or_authorize_upload(self):
        self.client.force_login(self.coder)
        current = self.empty_current_book()
        response = self.client.get(self.entry, follow=True)
        notice = self.setup_notice(response)
        self.assertIn('请联系负责人', notice)
        self.assertIn(f'href="/p/{self.project.pk}/books/{current.pk}/">查看规则', notice)
        self.assertNotIn('/import/', notice)
        self.assertEqual(self.client.get(reverse('book_import', args=[self.project.pk, current.pk])).status_code, 403)
        self.missing_units()
        response = self.client.get(self.entry, follow=True)
        notice = self.setup_notice(response)
        self.assertIn('请联系负责人', notice)
        self.assertIn(f'href="/p/{self.project.pk}/units/">查看材料', notice)
        self.assertNotIn('/materials/', notice)
        self.assertEqual(self.client.get(reverse('materials', args=[self.project.pk])).status_code, 403)
