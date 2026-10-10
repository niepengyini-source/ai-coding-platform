import csv
import hashlib
import io
import json
import random
import zipfile
from pathlib import Path
from collections import defaultdict
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import (Project, SourceDocument, SourceRecord, Unit, Codebook, Code, Round,
                     Assignment, Decision, AuditEvent)


class Conflict(ValueError):
    pass


def audit(project, actor, action, obj, before=None, after=None, reason=''):
    return AuditEvent.objects.create(project=project, actor=actor, action=action,
                                    object_type=type(obj).__name__, object_id=str(obj.pk),
                                    before=before or {}, after=after or {}, reason=reason)


def annotation_data(a):
    return {'primary': a.primary_id, 'secondary': a.secondary, 'uncertain': a.uncertain,
            'no_code': a.no_code, 'note': a.note, 'revision': a.revision,
            **({'status': a.status} if hasattr(a, 'status') else {'cycle': a.cycle})}


def validate_labels(book, data, submitting=True):
    try:
        if isinstance(data.get('primary'), bool):
            raise ValueError
        primary = int(data['primary']) if data.get('primary') not in ('', None) else None
        secondary = data.get('secondary', [])
        if not isinstance(secondary, list) or any(isinstance(x, bool) for x in secondary):
            raise ValueError
        secondary = [int(x) for x in secondary]
    except (TypeError, ValueError, KeyError):
        raise ValueError('代码格式不正确。')
    if len(secondary) > 100 or len(set(secondary)) != len(secondary) or primary in secondary:
        raise ValueError('主次代码不能重复。')
    for key in ('uncertain', 'no_code'):
        if not isinstance(data.get(key, False), bool):
            raise ValueError('疑点和无代码标记必须为布尔值。')
    no_code, uncertain = data.get('no_code', False), data.get('uncertain', False)
    if not isinstance(data.get('note', ''), str) or len(data.get('note', '')) > 5000:
        raise ValueError('备注最多5000个字符。')
    if no_code and (primary or secondary):
        raise ValueError('明确无适用代码时，不同时填写标签。')
    selected = ([primary] if primary else []) + secondary
    if book.codes.filter(id__in=selected).count() != len(selected):
        raise ValueError('只能使用本轮编码本中的代码。')
    if book.policy == 'single' and secondary:
        raise ValueError('这个项目版本使用单标签，不填写次代码。')
    if secondary and primary is None:
        raise ValueError('填写次代码时需要先选择主代码。')
    if submitting and not (primary or no_code or uncertain):
        raise ValueError('提交时请选择代码，或明确无代码，或标记不确定。')
    if uncertain and not data.get('note', '').strip():
        raise ValueError('不确定时请说明原因。')
    return {'primary_id': primary, 'secondary': secondary, 'no_code': no_code,
            'uncertain': uncertain, 'note': data.get('note', '').strip()}


@transaction.atomic
def import_records(project, actor, draft, rows, text_column, context_column):
    Project.objects.select_for_update().get(pk=project.pk)
    digest = hashlib.sha256(bytes(draft.raw)).hexdigest()
    if SourceDocument.objects.filter(project=project, sha256=digest).exists():
        raise ValueError('该文件已导入，未重复增加记录。')
    doc = SourceDocument.objects.create(project=project, name=Path(draft.filename).name,
        raw=draft.raw, sha256=digest, text_column=text_column, context_column=context_column,
        import_note='保留原始文件字节和所有原始列；空白文本不生成编码单元；不自动删除重复文字。')
    records = []
    for pos, row in enumerate(rows, 1):
        context_value = str(row.get(context_column, '')) if context_column else ''
        if len(context_value) > 450:
            raise ValueError('上下文分组值最长450个字符，不能静默截断后合并不同材料。')
        context_key = ('value:' + context_value if context_value.strip() else f'missing:{pos}') if context_column else ''
        records.append(SourceRecord(document=doc, position=pos, text=row[text_column],
            metadata={k: v for k, v in row.items() if k != text_column},
            context_key=context_key))
    SourceRecord.objects.bulk_create(records)
    Unit.objects.bulk_create([Unit(record=r, text=r.text, start=0, end=len(r.text)) for r in records if r.text.strip()])
    audit(project, actor, 'import', doc, after={'records': len(records), 'units': sum(bool(r.text.strip()) for r in records),
                                              'text_column': text_column, 'context_column': context_column})
    return doc


@transaction.atomic
def clone_book(project, actor, source, title, policy, change_note):
    Project.objects.select_for_update().get(pk=project.pk)
    version = (project.codebooks.order_by('-version').values_list('version', flat=True).first() or 0) + 1
    book = Codebook.objects.create(project=project, version=version, title=title, policy=policy, change_note=change_note)
    if source:
        mapping = {}
        old_codes = list(source.codes.all())
        for old in old_codes:
            values = {f.name: getattr(old, f.name) for f in Code._meta.fields if f.name not in ('id', 'book', 'parent')}
            mapping[old.id] = Code.objects.create(book=book, **values)
        for old in old_codes:
            if old.parent_id:
                mapping[old.id].parent = mapping[old.parent_id]
                mapping[old.id].save(update_fields=['parent'])
    audit(project, actor, 'codebook.new_version', book, after={'version': version, 'from': source.pk if source else None}, reason=change_note)
    return book


@transaction.atomic
def create_round(project, actor, book_id, name, kind, coder_ids, count, seed, distribution, double_percent, stratify='', unit_ids=None):
    Project.objects.select_for_update().get(pk=project.pk)
    if kind == 'quick' and (coder_ids != [actor.pk] or count or distribution != 'all' or stratify or unit_ids):
        raise ValueError('快速开始只能为本人编码全部有效单元，不分配他人任务。')
    book = Codebook.objects.select_for_update().get(project=project, pk=book_id)
    if not book.codes.exists():
        raise ValueError('请先填写至少一个代码及其定义。')
    valid_coders = set(project.memberships.exclude(role='viewer').filter(user__is_active=True).values_list('user_id', flat=True))
    if not coder_ids or not set(coder_ids).issubset(valid_coders):
        raise ValueError('编码员必须是本项目可编码的成员。')
    units = list(Unit.objects.filter(record__document__project=project, active=True).select_related('record'))
    requested_ids = set(unit_ids or [])
    if requested_ids:
        available_ids = {u.id for u in units}
        if not requested_ids.issubset(available_ids):
            raise ValueError('指定编号必须是本项目当前有效的编码单元；已拆分、合并或排除的旧编号不能再分配。')
        units = [u for u in units if u.id in requested_ids]
    if not units:
        raise ValueError('请先导入材料并确认编码单元。')
    count = count or len(units)
    if count > len(units):
        raise ValueError('抽样数量超过现有单元数。')
    rng = random.Random(seed)
    if kind == 'quick':
        selected = units
    elif stratify:
        groups = defaultdict(list)
        for unit in units:
            groups[str(unit.record.metadata.get(stratify, ''))].append(unit)
        ordered = list(groups.values())
        rng.shuffle(ordered)
        for group in ordered:
            rng.shuffle(group)
        selected = []
        while len(selected) < count:
            for group in ordered:
                if group and len(selected) < count:
                    selected.append(group.pop())
    else:
        selected = rng.sample(units, count)
    visible_ids = sorted(set(Assignment.objects.filter(round__project=project, unit_id__in=[u.id for u in selected])
                             .filter(Q(round__kind='training') | Q(round__state__in=['review', 'closed']))
                             .values_list('unit_id', flat=True)))
    book.frozen = True
    book.revision += 1
    book.save(update_fields=['frozen', 'revision'])
    r = Round.objects.create(project=project, book=book, name=name, kind=kind,
        started_by=actor if kind == 'quick' else None,
        sampling={'method': '个人快速开始：全部有效单元，按原文顺序' if kind == 'quick' else ('指定单元池＋' if requested_ids else '') + ('分层轮流抽样' if stratify else '随机抽样'), 'seed': seed, 'stratify': stratify,
                  'specified_pool_ids': sorted(requested_ids),
                  'previously_visible_unit_ids': visible_ids,
                  'count': count, 'distribution': distribution, 'double_percent': double_percent,
                  'unit_ids': [u.id for u in selected], 'coder_ids': coder_ids})
    assignments = []
    double_count = round(count * double_percent / 100)
    for index, unit in enumerate(selected):
        if distribution == 'all':
            chosen = coder_ids
        elif distribution == 'mixed' and index < double_count and len(coder_ids) >= 2:
            chosen = [coder_ids[index % len(coder_ids)], coder_ids[(index + 1) % len(coder_ids)]]
        else:
            chosen = [coder_ids[index % len(coder_ids)]]
        assignments.extend(Assignment(round=r, unit=unit, coder_id=c) for c in chosen)
    Assignment.objects.bulk_create(assignments)
    audit(project, actor, 'round.create', r, after=r.sampling)
    return r


@transaction.atomic
def save_assignment(assignment_id, actor, data):
    ref = Assignment.objects.only('round_id').get(pk=assignment_id, coder=actor)
    r = Round.objects.select_for_update().get(pk=ref.round_id)
    a = Assignment.objects.select_for_update().get(pk=assignment_id, coder=actor)
    token = str(data.get('token', ''))
    if not 8 <= len(token) <= 64:
        raise ValueError('缺少有效保存标识，请刷新页面重试。')
    if token == a.last_token:
        return a  # safe replay of an already committed request
    if r.state != 'active' or a.status == 'submitted':
        raise Conflict('本任务已提交或本轮已锁定，不能继续覆盖。请联系负责人。')
    if data.get('revision') != a.revision:
        raise Conflict('另一页面已经保存新版本。你的输入仍保留，请核对后刷新，不要直接覆盖。')
    if data.get('action') not in ('save', 'submit'):
        raise ValueError('无效保存操作。')
    values = validate_labels(r.book, data, submitting=data['action'] == 'submit')
    old = annotation_data(a)
    for key, value in values.items():
        setattr(a, key, value)
    a.revision += 1
    a.last_token = token
    a.status = 'submitted' if data['action'] == 'submit' else 'draft'
    a.save()
    audit(r.project, actor, 'annotation.' + data['action'], a, before=old, after=annotation_data(a))
    return a


@transaction.atomic
def save_decision(r, unit, actor, data):
    r = Round.objects.select_for_update().get(pk=r.pk)
    if r.state != 'review':
        raise Conflict('本轮不处于协商阶段。')
    current = Decision.objects.filter(round=r, unit=unit).first()
    revision = current.revision if current else 0
    if data.get('revision') != revision:
        raise Conflict('他人已修改这条协商结论。输入仍保留，请先核对最新版本。')
    values = validate_labels(r.book, data)
    if not values['note']:
        raise ValueError('协商结论需要写明判断依据。')
    old = annotation_data(current) if current else {}
    if not current:
        current = Decision(round=r, unit=unit, author=actor)
    for key, value in values.items():
        setattr(current, key, value)
    current.author, current.cycle, current.revision = actor, r.cycle, revision + 1
    current.save()
    audit(r.project, actor, 'decision.save', current, before=old, after=annotation_data(current), reason=values['note'])
    return current


def csv_safe(value):
    text = str(value) if value is not None else ''
    # Do not allow exported research text to become spreadsheet formulas.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def csv_bytes(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    return stream.getvalue().encode('utf-8-sig')


def export_annotations(r, queryset):
    from .exporting import annotation_table
    table = annotation_table(r, queryset)
    return csv_bytes(table.headers, table.rows)


def export_final(r):
    from .exporting import final_table
    table = final_table(r)
    return csv_bytes(table.headers, table.rows)


def export_disagreements(r):
    from .exporting import disagreement_table
    table = disagreement_table(r)
    return csv_bytes(table.headers, table.rows)


def project_archive(project):
    files = {}
    def add_json(name, value):
        files[name] = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode('utf-8')
    add_json('project.json', {'id': project.id, 'name': project.name, 'goal': project.goal, 'description': project.description,
             'members': list(project.memberships.values('user__username', 'role')), 'format_version': 1})
    for doc in project.documents.all():
        extension = Path(doc.name).suffix.lower()
        files[f'00_raw/document_{doc.id}{extension}'] = bytes(doc.raw)
        add_json(f'00_raw/document_{doc.id}_manifest.json', {'original_name': doc.name, 'sha256': doc.sha256,
                 'text_column': doc.text_column, 'context_column': doc.context_column, 'cleaning_note': doc.import_note})
    add_json('01_units/records.json', list(SourceRecord.objects.filter(document__project=project).values()))
    add_json('01_units/units.json', list(Unit.objects.filter(record__document__project=project).values()))
    for book in project.codebooks.all():
        add_json(f'02_codebook/v{book.version}.json', {'version': book.version, 'policy': book.policy,
                 'title': book.title, 'frozen': book.frozen, 'change_note': book.change_note, 'codes': list(book.codes.values())})
        for source in book.import_sources.all():
            path = f'02_codebook/import_{source.id}{Path(source.filename).suffix.lower()}'
            files[path] = bytes(source.raw)
            add_json(f'02_codebook/import_{source.id}_manifest.json', {'original_name': source.filename,
                     'book_version': book.version, 'preview': source.preview,
                     'sha256': hashlib.sha256(bytes(source.raw)).hexdigest()})
    for r in project.rounds.all():
        folder = '03_personal_coding' if r.kind == 'quick' else ('03_pilot' if r.kind != 'formal' else '04_formal_coding')
        files[f'{folder}/round_{r.id}_independent.csv'] = export_annotations(r, r.assignments.all())
        add_json(f'{folder}/round_{r.id}_settings.json', {'name': r.name, 'kind': r.kind, 'book_version': r.book.version,
                                                        'cycle': r.cycle, 'sampling': r.sampling,
                                                        'started_by': r.started_by_id,
                                                        'note': '个人编码记录，不是多人协商定稿或独立一致性评估。' if r.kind == 'quick' else ''})
        add_json(f'{folder}/round_{r.id}_reports.json', list(r.reports.values()))
        add_json(f'05_adjudication/round_{r.id}_decisions.json', list(r.decisions.values()))
        from .models import Comment
        add_json(f'05_adjudication/round_{r.id}_comments.json', list(Comment.objects.filter(round=r).values()))
        files[f'05_adjudication/round_{r.id}_disagreements.csv'] = export_disagreements(r)
        if r.kind != 'quick':
            files[f'06_final/round_{r.id}.csv'] = export_final(r)
    add_json('99_logs/audit.json', list(AuditEvent.objects.filter(project=project).values()))
    add_json('manifest.json', {'created_at': timezone.now().isoformat(), 'format_version': 1,
        'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()},
        'note': '研究项目归档，不含账号密码或服务器密钥；不是完整服务器备份。'})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()
