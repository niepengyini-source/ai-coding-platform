"""Direct import, single secondary codes and in-workbench segmentation regressions."""
import json
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Assignment, AuditEvent, Code, Codebook, Comment, Decision, Unit
from .reliability import report_for
from .services import annotation_data, save_assignment
from .tests import TEST_SETTINGS, fixture
from .workflow import history_permissions, start_quick_coding


@override_settings(**TEST_SETTINGS)
class WorkpadTests(TestCase):
    def setUp(self):
        for key, value in fixture().items():
            setattr(self, key, value)
        Codebook.objects.filter(pk=self.book.pk).update(policy='multi')
        self.book.refresh_from_db()
        self.third = Code.objects.create(book=self.book, key='ADJUST', name='调整', definition='改变做法')
        self.client.force_login(self.coder)
        self.personal, _ = start_quick_coding(self.project, self.coder, self.book.pk, uuid.uuid4())
        self.a = self.personal.assignments.get(unit=self.units[0])

    def split(self, a=None, **changes):
        a = a or self.a
        data = {'start': 2, 'end': 6, 'revision': a.revision, 'token': str(uuid.uuid4()), **changes}
        return self.client.post(f'/api/assignments/{a.pk}/split/', json.dumps(data), content_type='application/json'), data

    def save(self, a=None, **changes):
        a = a or self.a
        result = save_assignment(a.pk, self.coder, {'revision': a.revision, 'primary': self.first.pk,
            'secondary': [self.second.pk], 'no_code': False, 'uncertain': False, 'note': '虚构判断',
            'token': str(uuid.uuid4()), 'action': 'save', **changes})
        a.refresh_from_db()
        return result

    def test_project_overview_has_direct_upload_for_owner_and_reviewer_only(self):
        self.client.force_login(self.owner)
        response = self.client.get(f'/p/{self.project.pk}/')
        self.assertContains(response, 'id="project-material-file"')
        self.assertContains(response, f'action="/p/{self.project.pk}/materials/"')
        self.assertContains(response, 'multipart/form-data')
        for user in [self.coder, self.viewer]:
            self.client.force_login(user)
            self.assertNotContains(self.client.get(f'/p/{self.project.pk}/'), 'id="project-material-file"')

    def test_direct_upload_uses_existing_preview_and_confirmation(self):
        self.client.force_login(self.owner)
        uploaded = SimpleUploadedFile('workpad.csv', 'room_id,content\nR1,先讨论目标。\nR1,再检查进度。\n'.encode())
        response = self.client.post(f'/p/{self.project.pk}/materials/', {'file': uploaded})
        self.assertContains(response, '核对材料')
        draft = response.context['draft']
        response = self.client.post(f'/p/{self.project.pk}/materials/',
            {'draft_id': str(draft.pk), 'text_column': 'content', 'context_column': 'room_id'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Unit.objects.filter(record__document__project=self.project, active=True).count(), 5)

    def test_primary_and_secondary_can_be_saved_together(self):
        self.save()
        self.assertEqual(self.a.primary_id, self.first.pk)
        self.assertEqual(self.a.secondary, [self.second.pk])
        self.save(secondary=[self.third.pk])
        self.assertEqual(self.a.primary_id, self.first.pk)
        self.assertEqual(self.a.secondary, [self.third.pk])
        self.save(secondary=[])
        self.assertEqual(self.a.secondary, [])

    def test_multiple_or_repeated_secondary_codes_are_rejected_without_overwrite(self):
        original = annotation_data(self.a)
        for secondary in [[self.second.pk, self.third.pk], [self.first.pk], [self.foreign_code.pk]]:
            with self.assertRaises(ValueError):
                self.save(secondary=secondary)
            self.a.refresh_from_db()
            self.assertEqual(annotation_data(self.a), original)

    def test_secondary_is_one_radio_choice_and_has_clear_control(self):
        response = self.client.get(f'/r/{self.personal.pk}/?a={self.a.pk}')
        self.assertContains(response, 'type="radio" name="secondary"')
        self.assertNotContains(response, 'type="checkbox" name="secondary"')
        self.assertContains(response, 'data-secondary-clear')

    def test_middle_selection_has_three_lossless_pieces_and_opens_selected_piece(self):
        original_other = list(self.r.assignments.values())
        response, data = self.split()
        self.assertEqual(response.status_code, 200)
        children = list(self.a.segments.select_related('unit').order_by('unit__start'))
        self.assertEqual([(c.unit.start,c.unit.end) for c in children], [(0,2),(2,6),(6,len(self.a.unit.text))])
        self.assertEqual(''.join(c.unit.text for c in children), self.a.unit.text)
        self.assertEqual(response.json()['next_url'], f'/r/{self.personal.pk}/?a={children[1].pk}')
        self.assertTrue(all(not c.unit.active and not c.primary_id and not c.secondary for c in children))
        self.assertEqual(original_other, list(self.r.assignments.values()))
        replay, _ = self.split(**data)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.a.segments.count(), 3)
        self.assertEqual(AuditEvent.objects.filter(action='annotation.split',object_id=str(self.a.pk)).count(), 1)

    def test_saved_and_submitted_labels_remain_in_read_only_parent_history(self):
        for action in ['save','submit']:
            a = self.personal.assignments.get(unit=self.units[0 if action=='save' else 1])
            self.save(a, action=action)
            old = annotation_data(a)
            response, _ = self.split(a,start=0,end=2)
            self.assertEqual(response.status_code,200)
            a.refresh_from_db()
            self.assertTrue(a.superseded)
            self.assertEqual(a.primary_id,old['primary'])
            self.assertEqual(a.secondary,old['secondary'])
            self.assertEqual(a.note,old['note'])
            self.assertFalse(history_permissions(a,self.coder,'coder')['editable'])
            self.assertContains(self.client.get(f'/p/{self.project.pk}/history/{a.pk}/'),'原记录已拆分')
            with self.assertRaises(ValueError):
                self.save(a)

    def test_each_new_piece_can_be_saved_and_split_again(self):
        self.split()
        child = self.a.segments.order_by('unit__start').last()
        self.save(child)
        response, _ = self.split(child,start=0,end=1)
        self.assertEqual(response.status_code,200)
        live = self.personal.assignments.filter(superseded=False,unit__record=self.a.unit.record).select_related('unit').order_by('unit__start')
        self.assertEqual(''.join(a.unit.text for a in live),self.a.unit.text)
        for index,a in enumerate(live):
            self.save(a, primary=self.first.pk if index%2 else self.second.pk,secondary=[])
        self.assertTrue(all(a.revision>0 for a in live))

    def test_invalid_ranges_and_stale_revisions_never_create_pieces(self):
        for start,end in [(0,len(self.a.unit.text)),(-1,3),(2,2),(4,3),(True,4),(0,999),('1',3),(1,None)]:
            response,_ = self.split(start=start,end=end)
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.split(revision=999)[0].status_code,409)
        self.assertFalse(self.a.segments.exists())

    def test_invalid_split_tokens_do_not_change_original_or_create_pieces(self):
        original = annotation_data(self.a)
        for token in ['', 'invalid-token', None, {}]:
            self.assertEqual(self.split(token=token)[0].status_code, 400)
        self.a.refresh_from_db()
        self.assertEqual(annotation_data(self.a), original)
        self.assertFalse(self.a.superseded)
        self.assertFalse(self.a.segments.exists())

    def test_whitespace_neighbours_are_preserved_without_empty_fragments(self):
        record = self.a.unit.record
        record.text = '  第一段。\n第二段。  '
        record.save(update_fields=['text'])
        self.a.unit.text, self.a.unit.end = record.text, len(record.text)
        self.a.unit.save(update_fields=['text', 'end'])
        response, _ = self.split(start=2, end=6)
        self.assertEqual(response.status_code, 200)
        children = list(self.a.segments.select_related('unit').order_by('unit__start'))
        self.assertEqual(''.join(child.unit.text for child in children), record.text)
        self.assertEqual(children[0].unit.start, 0)
        self.assertEqual(children[-1].unit.end, len(record.text))
        self.assertTrue(all(child.unit.text.strip() for child in children))

    def test_saved_no_code_and_uncertainty_remain_only_on_parent(self):
        self.save(primary=None, secondary=[], no_code=True, uncertain=True)
        old = annotation_data(self.a)
        self.assertEqual(self.split()[0].status_code, 200)
        self.a.refresh_from_db()
        self.assertTrue(self.a.superseded)
        for field in ['primary', 'secondary', 'no_code', 'uncertain', 'note', 'status']:
            self.assertEqual(annotation_data(self.a)[field], old[field])
        self.assertTrue(all(not child.primary_id and not child.secondary and not child.no_code
                            and not child.uncertain and not child.note for child in self.a.segments.all()))

    def test_locked_round_and_foreign_user_cannot_split(self):
        self.personal.state='closed';self.personal.save(update_fields=['state'])
        self.assertEqual(self.split()[0].status_code,409)
        self.client.force_login(self.owner)
        self.assertEqual(self.split()[0].status_code,404)

    def test_shared_task_split_only_changes_own_work_and_matching_boundaries_align(self):
        mine=self.r.assignments.get(coder=self.coder,unit=self.units[0])
        other=self.r.assignments.get(coder=self.owner,unit=self.units[0])
        old=annotation_data(other)
        self.assertEqual(self.split(mine)[0].status_code,200)
        other.refresh_from_db();self.assertEqual(annotation_data(other),old)
        self.assertFalse(other.superseded)
        self.client.force_login(self.owner)
        self.assertEqual(self.split(other)[0].status_code,200)
        self.assertEqual(list(mine.segments.order_by('unit__start').values_list('unit_id',flat=True)),
                         list(other.segments.order_by('unit__start').values_list('unit_id',flat=True)))
        report=report_for(self.r)
        self.assertTrue(report['has_personal_splits'])
        self.assertNotIn(mine.pk,[row['id'] for row in report['snapshot']])
        self.assertNotIn(other.pk,[row['id'] for row in report['snapshot']])

    def test_different_shared_boundaries_are_not_compared_as_the_same_unit(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        self.assertEqual(self.split(mine, start=0, end=2)[0].status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.split(other, start=0, end=3)[0].status_code, 200)
        for a in self.r.assignments.filter(superseded=False):
            save_assignment(a.pk, a.coder, {'revision': a.revision, 'primary': self.first.pk,
                'secondary': [], 'no_code': False, 'uncertain': False, 'note': '虚构比较',
                'action': 'submit', 'token': str(uuid.uuid4())})
        report = report_for(self.r)
        self.assertTrue(report['has_personal_splits'])
        self.assertEqual(report['pairs'][0]['n'], 2)
        self.assertEqual(report['pairs'][0]['primary_agreement'], 1)

    def test_exposed_source_remains_exposed_after_both_coders_and_nested_splits(self):
        self.r.sampling['previously_visible_unit_ids'] = [self.units[0].pk]
        self.r.save(update_fields=['sampling'])
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        self.assertEqual(self.split(mine)[0].status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.split(other)[0].status_code, 200)
        initial_ids = set(mine.segments.values_list('unit_id', flat=True))
        report = report_for(self.r)
        self.assertEqual(set(report['previously_visible_unit_ids']), initial_ids)
        self.assertEqual(report['previously_visible_unit_count'], 3)
        self.assertNotIn(self.units[0].pk, report['previously_visible_unit_ids'])

        self.client.force_login(self.coder)
        child = mine.segments.order_by('unit__start').first()
        self.assertEqual(self.split(child, start=0, end=1)[0].status_code, 200)
        descendants = set(child.segments.values_list('unit_id', flat=True))
        expected = set(self.r.assignments.filter(superseded=False,
            unit__record=self.units[0].record).values_list('unit_id', flat=True))
        report = report_for(self.r)
        self.assertEqual(set(report['previously_visible_unit_ids']), expected)
        self.assertTrue(descendants <= expected)
        self.assertEqual(report['previously_visible_unit_count'], len(expected))
        self.assertNotIn(self.units[1].pk, expected)
        self.assertNotIn(self.units[2].pk, expected)
        self.r.refresh_from_db()
        self.assertEqual(self.r.sampling['previously_visible_unit_ids'], [self.units[0].pk])

    def test_shared_round_can_be_locked_and_reviewed_without_superseded_parents(self):
        mine=self.r.assignments.get(coder=self.coder,unit=self.units[0])
        self.split(mine)
        for a in self.r.assignments.filter(superseded=False):
            save_assignment(a.pk,a.coder,{'revision':a.revision,'primary':self.first.pk,'secondary':[],
                'no_code':False,'uncertain':False,'note':'虚构依据','action':'submit','token':str(uuid.uuid4())})
        self.r.refresh_from_db();self.client.force_login(self.owner)
        response=self.client.post(f'/r/{self.r.pk}/manage/',{'action':'lock','revision':self.r.revision})
        self.assertEqual(response.status_code,302)
        self.r.refresh_from_db();self.assertEqual(self.r.state,'review')
        response=self.client.get(f'/r/{self.r.pk}/review/')
        self.assertTrue(all(not a.superseded for a,secondary in response.context['entries']))

    def lock_shared_round(self):
        for a in self.r.assignments.filter(superseded=False):
            save_assignment(a.pk, a.coder, {'revision': a.revision, 'primary': self.first.pk,
                'secondary': [], 'no_code': False, 'uncertain': False, 'note': '虚构复核',
                'action': 'submit', 'token': str(uuid.uuid4())})
        self.r.refresh_from_db()
        self.client.force_login(self.owner)
        response = self.client.post(f'/r/{self.r.pk}/manage/', {'action': 'lock', 'revision': self.r.revision})
        self.assertEqual(response.status_code, 302)
        self.r.refresh_from_db()
        self.assertEqual(self.r.state, 'review')

    def test_parent_only_unit_cannot_receive_new_decisions_or_comments(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        other = self.r.assignments.get(coder=self.owner, unit=self.units[0])
        self.assertEqual(self.split(mine)[0].status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.split(other)[0].status_code, 200)
        self.lock_shared_round()
        response = self.client.post(f'/api/rounds/{self.r.pk}/units/{self.units[0].pk}/decision/',
            json.dumps({'revision': 0, 'primary': self.first.pk, 'secondary': [], 'no_code': False,
                        'uncertain': False, 'note': '不能新写入已替换单元'}), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        response = self.client.post(f'/r/{self.r.pk}/units/{self.units[0].pk}/comments/', {'body': '不能新增'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Decision.objects.filter(round=self.r, unit=self.units[0]).exists())
        self.assertFalse(Comment.objects.filter(round=self.r, unit=self.units[0]).exists())

    def test_unit_with_another_current_assignment_can_still_be_reviewed(self):
        mine = self.r.assignments.get(coder=self.coder, unit=self.units[0])
        self.assertEqual(self.split(mine)[0].status_code, 200)
        self.lock_shared_round()
        response = self.client.post(f'/api/rounds/{self.r.pk}/units/{self.units[0].pk}/decision/',
            json.dumps({'revision': 0, 'primary': self.first.pk, 'secondary': [], 'no_code': False,
                        'uncertain': False, 'note': '其他成员仍使用此单元'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f'/r/{self.r.pk}/units/{self.units[0].pk}/comments/', {'body': '有效讨论'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Decision.objects.filter(round=self.r, unit=self.units[0]).count(), 1)
        self.assertEqual(Comment.objects.filter(round=self.r, unit=self.units[0]).count(), 1)

    def test_removed_marketing_copy_is_not_on_login_or_quick_start(self):
        self.client.logout();response=self.client.get('/login/')
        self.assertNotContains(response,'独立答案隔离');self.assertNotContains(response,'规则版本留存')
        self.client.force_login(self.coder)
        self.assertNotContains(self.client.get(f'/p/{self.project.pk}/quick-start/'),'不需要填写任务安排表')
