from html.parser import HTMLParser

from django.core.cache import cache
from django.test import TestCase, override_settings

from .tests import TEST_SETTINGS, fixture


class NavigationDOM(HTMLParser):
    void_tags = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parents': self.stack.copy(), 'text': ''}
        self.nodes.append(node)
        if tag not in self.void_tags:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data

    def find(self, *, tag=None, class_name=None, element_id=None):
        return [node for node in self.nodes
                if (tag is None or node['tag'] == tag)
                and (class_name is None or class_name in node['attrs'].get('class', '').split())
                and (element_id is None or node['attrs'].get('id') == element_id)]

    def children(self, node, tag=None):
        return [child for child in self.nodes
                if any(parent is node for parent in child['parents'])
                and (tag is None or child['tag'] == tag)]


@override_settings(**TEST_SETTINGS)
class ProjectNavigationGroupTests(TestCase):
    def setUp(self):
        cache.clear()
        for name, value in fixture().items():
            setattr(self, name, value)

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return NavigationDOM(response.content.decode())

    def assert_group(self, dom):
        groups = dom.find(class_name='nav-project-group')
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['attrs'].get('data-project-id'), str(self.project.id))
        panels = dom.find(element_id='project-navigation')
        self.assertEqual(len(panels), 1)
        self.assertIn('hidden', panels[0]['attrs'])
        self.assertTrue(any(parent is groups[0] for parent in panels[0]['parents']))
        toggles = dom.find(tag='button', class_name='nav-project-toggle')
        self.assertEqual(len(toggles), 1)
        self.assertEqual(toggles[0]['attrs'].get('type'), 'button')
        self.assertEqual(toggles[0]['attrs'].get('aria-controls'), 'project-navigation')
        self.assertEqual(toggles[0]['attrs'].get('aria-expanded'), 'false')
        self.assertTrue(toggles[0]['attrs'].get('aria-label'))
        self.assertTrue(any(parent is groups[0] for parent in toggles[0]['parents']))
        return groups[0], panels[0]

    def test_owner_coder_and_viewer_keep_authorized_links_inside_closed_group(self):
        base = f'/p/{self.project.id}'
        common = {f'{base}/units/', f'{base}/books/{self.book.id}/',
                  f'{base}/history/', f'{base}/exports/', f'{base}/archive/'}
        for user in [self.owner, self.coder, self.viewer]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                dom = self.page(f'{base}/')
                group, panel = self.assert_group(dom)
                links = dom.children(panel, 'a')
                expected = common | ({f'{base}/code/'} if user != self.viewer else set())
                self.assertEqual({link['attrs'].get('href') for link in links}, expected)
                overview = [link for link in dom.children(group, 'a')
                            if link['attrs'].get('href') == f'{base}/']
                self.assertEqual(len(overview), 1)
                self.assertFalse(any(parent is panel for parent in overview[0]['parents']))
                self.assertEqual(overview[0]['text'].strip(), self.project.name)

    def test_global_entries_and_management_are_not_nested_in_project_group(self):
        self.client.force_login(self.owner)
        dom = self.page(f'/p/{self.project.id}/')
        group, panel = self.assert_group(dom)
        sidebar = dom.find(tag='aside', class_name='sidebar')[0]
        links = dom.children(sidebar, 'a')
        for href in ['/projects/', '/groups/join/', f'/guide/?project={self.project.id}']:
            with self.subTest(href=href):
                matching = [link for link in links if link['attrs'].get('href') == href]
                self.assertEqual(len(matching), 1)
                self.assertFalse(any(parent is group for parent in matching[0]['parents']))
        management = dom.find(tag='details', class_name='nav-management')
        self.assertEqual(len(management), 1)
        self.assertFalse(any(parent is panel or parent is group
                             for parent in management[0]['parents']))

    def test_project_pages_keep_same_collapsed_navigation(self):
        self.client.force_login(self.owner)
        base = f'/p/{self.project.id}'
        for path in [f'{base}/units/', f'{base}/materials/',
                     f'{base}/books/{self.book.id}/', f'{base}/history/',
                     f'{base}/progress/', f'{base}/exports/', f'{base}/archive/',
                     f'/r/{self.r.id}/']:
            with self.subTest(path=path):
                self.assert_group(self.page(path))

    def test_project_help_preserves_group_and_round_help_link(self):
        self.client.force_login(self.coder)
        path = f'/guide/?project={self.project.id}&round={self.r.id}'
        dom = self.page(path)
        self.assert_group(dom)
        self.assertTrue(any(link['attrs'].get('href') == path
                            for link in dom.find(tag='a')))

    def test_workspace_pages_do_not_expand_project_navigation(self):
        self.client.force_login(self.owner)
        self.page(f'/p/{self.project.id}/')
        for path in ['/', '/projects/', '/groups/join/', '/guide/']:
            with self.subTest(path=path):
                dom = self.page(path)
                self.assertEqual(dom.find(class_name='nav-project-group'), [])
                self.assertEqual(dom.find(element_id='project-navigation'), [])
                self.assertEqual(dom.find(class_name='nav-project-toggle'), [])

    def test_foreign_project_and_help_do_not_expose_group_or_project_name(self):
        self.client.force_login(self.coder)
        for path in [f'/p/{self.other.id}/', f'/guide/?project={self.other.id}']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                dom = NavigationDOM(response.content.decode())
                self.assertEqual(dom.find(class_name='nav-project-group'), [])
                self.assertNotContains(response, self.other.name, status_code=404)

    def test_project_heading_uses_exact_saved_name_without_added_prefix(self):
        self.client.force_login(self.owner)
        for name in ['访谈研究 Study A', '项目 · Phase 2 / 阶段二',
                     '工作引导 · 团队虚构验收 · d8df1ba2ee', '中文 & English <研究>']:
            with self.subTest(name=name):
                self.project.name = name
                self.project.save(update_fields=['name'])
                dom = self.page(f'/p/{self.project.id}/')
                headings = dom.find(tag='h1')
                self.assertEqual(len(headings), 1)
                self.assertEqual(headings[0]['text'], name)
                group, _ = self.assert_group(dom)
                labels = [link for link in dom.children(group, 'a')
                          if link['attrs'].get('href') == f'/p/{self.project.id}/']
                self.assertEqual(len(labels), 1)
                self.assertEqual(labels[0]['text'].strip(), name)
                topbar = dom.find(class_name='topbar-label')
                self.assertEqual(len(topbar), 1)
                self.assertEqual(topbar[0]['text'], name)
