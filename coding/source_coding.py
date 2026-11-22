"""Optional source coding; source files and preassigned Unit tasks are never rewritten."""
import hashlib
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import AuditEvent, Code, Codebook, Membership, Project, Round, SourceDocument
from .source_models import SourceAnnotation, SourceCodingTask
from .services import Conflict, audit, clone_book, validate_labels


MAX_ANNOTATIONS = 10000
MAX_SELECTION_CHARS = 100000


def canonical_source(document):
    """One immutable reading text, retaining real record positions after old deduplication."""
    records = list(document.records.order_by('position'))
    normalized = document.normalized or {}
    if normalized.get('type') == 'image':
        return '', []
    if normalized.get('type') == 'document':
        if not records or normalized.get('text') != records[0].text:
            raise ValueError('阅读文本与原材料无法对应，请先核对材料。')
        return records[0].text, normalized.get('blocks') or [{'id': '1', 'start': 0,
            'end': len(records[0].text), 'text': records[0].text, 'page': None, 'heading': '', 'level': 0}]
    blocks, texts, offset = [], [], 0
    for record in records:
        text = record.text.replace('\r\n', '\n').replace('\r', '\n')
        if blocks:
            offset += 2
            texts.append('\n\n')
        blocks.append({'id': str(record.pk), 'text': text, 'start': offset, 'end': offset + len(text),
                       'row': record.position, 'record_id': record.pk, 'metadata': record.metadata,
                       'page': None, 'heading': '', 'level': 0})
        texts.append(text)
        offset += len(text)
    return ''.join(texts), blocks


def progress_for(round_obj, actor):
    tasks = SourceCodingTask.objects.filter(round=round_obj, coder=actor)
    return {'done': tasks.filter(completed=True).count(), 'total': tasks.count(),
            'annotations': SourceAnnotation.objects.filter(task__in=tasks).count()}


def code_data(code):
    return {'id': code.pk, 'key': code.key, 'name': code.name, 'definition': code.definition}


def source_annotation_data(annotation, overview=False):
    snapshot = annotation.book_snapshot
    return {'id': annotation.pk, 'start': annotation.start, 'end': annotation.end,
            'region': annotation.region,
            'text': getattr(annotation, '_preview', '') if overview else annotation.text,
            'primary': annotation.primary_id, 'secondary': annotation.secondary, 'labels': annotation.labels,
            'no_code': annotation.no_code, 'uncertain': annotation.uncertain, 'note': '' if overview else annotation.note,
            'revision': annotation.revision, 'book_id': snapshot.get('id'),
            'book_version': snapshot.get('version'), 'policy': snapshot.get('policy'),
            'code_names': [entry['name'] for entry in snapshot.get('codes', [])],
            'code_keys': [entry['key'] for entry in snapshot.get('codes', [])],
            'updated_at': annotation.updated_at.isoformat() if annotation.pk else None,
            'book_snapshot': {key: snapshot.get(key) for key in ('id', 'version', 'policy')} if overview else snapshot}


def task_data(task):
    return {'id': task.pk, 'completed': task.completed, 'revision': task.revision}


def _member(project_id, actor, editable=True):
    role = Membership.objects.filter(project_id=project_id, user=actor, user__is_active=True).values_list('role', flat=True).first()
    if role is None or editable and role == 'viewer':
        raise PermissionDenied
    return role


def _lock_round(round_id, actor):
    project_id = Round.objects.values_list('project_id', flat=True).get(pk=round_id)
    Project.objects.select_for_update().get(pk=project_id)
    round_obj = Round.objects.select_for_update().select_related('book', 'project').get(pk=round_id)
    _member(project_id, actor)
    return round_obj


def _integer(value, label):
    if isinstance(value, bool):
        raise ValueError(label + '格式不正确。')
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal() or len(value) > 18:
            raise ValueError(label + '格式不正确。')
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValueError(label + '格式不正确。')
    return value


def coding_documents(project):
    preview_only = project.documents.filter(normalized__source__format='pdf', normalized__text='')
    return project.documents.filter(records__isnull=False).exclude(pk__in=preview_only).distinct().order_by('id')


@transaction.atomic
def start_source_coding(project, actor, book_id, document_ids=None):
    Project.objects.select_for_update().get(pk=project.pk)
    role = _member(project.pk, actor)
    requested = Codebook.objects.filter(project=project, pk=book_id).first()
    current = project.codebooks.first()
    if not requested or not current:
        raise ValueError('项目编码本不可用，请刷新后重试。')
    documents = coding_documents(project)
    if document_ids is not None:
        ids = {_integer(value, '材料编号') for value in document_ids}
        documents = documents.filter(pk__in=ids)
        if not ids or documents.count() != len(ids):
            raise ValueError('请选择本项目已导入的材料。')
    documents = list(documents)
    if not any(document.normalized.get('type') == 'image' or
               any(record.text.strip() for record in document.records.all()) for document in documents):
        raise ValueError('请先导入图片或具有可读文字的材料。')
    existing = Round.objects.filter(project=project, kind='quick', started_by=actor, state='active',
        sampling__source_coding=True, book_id__in={requested.pk, current.pk}).order_by('-id').first()
    if existing:
        for document in documents:
            SourceCodingTask.objects.get_or_create(round=existing, document=document, coder=actor)
        return existing, False
    if requested.pk != current.pk:
        raise Conflict('编码本已更新，请刷新后使用当前规则。')
    book = requested
    if book.frozen and role in ('owner', 'reviewer'):
        book = clone_book(project, actor, book, book.title, book.policy, '原文选段使用独立可编辑稿，保留旧任务规则。')
    round_obj = Round.objects.create(project=project, book=book, name='我的原文编码', kind='quick', started_by=actor,
        sampling={'source_coding': True, 'open_coding': True, 'document_ids': [d.pk for d in documents]})
    SourceCodingTask.objects.bulk_create([SourceCodingTask(round=round_obj, document=d, coder=actor) for d in documents])
    audit(project, actor, 'source.start', round_obj,
          after={'documents': [d.pk for d in documents], 'book_id': book.pk, 'personal': True})
    return round_obj, True


@transaction.atomic
def add_workspace_code(round_id, actor, data):
    round_obj = _lock_round(round_id, actor)
    code = _add_workspace_code(round_obj, actor, data)
    book = round_obj.book
    return {'code': code_data(code), 'codes': [code_data(c) for c in book.codes.all()],
            'book_id': book.pk, 'book_revision': book.revision}


def _add_workspace_code(round_obj, actor, data, *, keep_existing_definition=False):
    if _member(round_obj.project_id, actor) not in ('owner', 'reviewer'):
        raise PermissionDenied
    if round_obj.state != 'active' or not (round_obj.sampling.get('open_coding') or round_obj.sampling.get('source_coding')):
        raise ValueError('本批次使用固定规则，请在编码本管理中更新规则。')
    book = round_obj.book
    if _integer(data.get('book_id'), '编码本编号') != book.pk or _integer(data.get('revision'), '编码本版本') != book.revision:
        raise Conflict('编码规则已变化，输入仍保留，请刷新代码列表后再试。')
    if book.frozen:
        raise Conflict('当前规则已用于固定任务。请通过“更新编码本”生成可编辑版本，旧记录不会被覆盖。')
    name, definition = data.get('name'), data.get('definition', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('请填写代码名称，最多100个字符。')
    if not isinstance(definition, str) or len(definition) > 20000:
        raise ValueError('代码定义最多20000个字符。')
    name, definition = name.strip(), definition.strip()
    existing = book.codes.filter(name__iexact=name).first()
    if existing:
        if existing.definition != definition and (definition or not keep_existing_definition):
            raise ValueError('已有相同名称的代码，请复用或在编码本中编辑定义。')
        code = existing
    else:
        keys = set(book.codes.values_list('key', flat=True))
        index = 1
        while f'C{index:03d}' in keys:
            index += 1
        code = Code.objects.create(book=book, key=f'C{index:03d}', name=name, definition=definition)
        book.revision += 1
        book.save(update_fields=['revision'])
        audit(round_obj.project, actor, 'code.add_during_coding', code,
              after={'book_id': book.pk, **code_data(code)})
    return code


def _image_region(value):
    keys = ('x', 'y', 'width', 'height')
    error = '请选择图片范围内有效的矩形区域。'
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(error)
    if any(type(value[key]) not in (int, float) or not 0 <= value[key] <= 1 for key in keys):
        raise ValueError(error)
    x, y, width, height = coordinates = [round(value[key] * 10000) for key in keys]
    if not width or not height or x + width > 10000 or y + height > 10000:
        raise ValueError(error)
    return dict(zip(keys, (coordinate / 10000 for coordinate in coordinates)))


@transaction.atomic
def save_source_annotation(task_id, actor, data):
    reference = SourceCodingTask.objects.only('round_id').get(pk=task_id, coder=actor)
    round_obj = _lock_round(reference.round_id, actor)
    task = SourceCodingTask.objects.select_for_update().select_related('document').get(pk=task_id, coder=actor)
    if not round_obj.sampling.get('source_coding') or round_obj.state != 'active':
        raise Conflict('本轮已结束，编码记录只读保留。')
    if task.completed:
        raise Conflict('本份材料已标记完成，请先重新打开再修改编码。')
    if _integer(data.get('book_id'), '编码本编号') != round_obj.book_id:
        raise Conflict('编码规则已更新，请刷新后核对再保存。')
    text, _ = canonical_source(task.document)
    region = None
    if task.document.normalized.get('type') == 'image':
        region = _image_region(data.get('region'))
        start = end = 0
    else:
        if data.get('region') is not None:
            raise ValueError('此材料请使用文字选段编码。')
        start, end = _integer(data.get('start'), '原文起点'), _integer(data.get('end'), '原文终点')
        if not 0 <= start < end <= len(text) or end - start > MAX_SELECTION_CHARS or not text[start:end].strip():
            raise ValueError('请选择有效的连续文字，单个片段最多100000个字符。')
        selected_text = data.get('text')
        if not isinstance(selected_text, str) or selected_text.replace('\r\n', '\n') != text[start:end]:
            raise ValueError('所选文字与原文位置不一致，请重新划选。')
    token = data.get('token')
    if not isinstance(token, str) or not 8 <= len(token) <= 64:
        raise ValueError('缺少保存标识，请刷新后重试。')
    annotation = task.annotations.filter(start=start, end=end, region=region).first() if region else (
        task.annotations.filter(start=start, end=end, region__isnull=True).first())
    if annotation and annotation.last_token == token:
        return annotation
    revision = _integer(data.get('revision'), '保存版本')
    if revision != (annotation.revision if annotation else 0):
        raise Conflict('该范围已有更新，请先打开自己已有的编码记录，再修改保存。')
    if annotation is None and task.annotations.count() >= MAX_ANNOTATIONS:
        raise ValueError('单份材料最多保存10000个片段，请先整理或拆分材料。')
    new_code = data.get('new_code')
    if new_code is not None:
        if not isinstance(new_code, dict):
            raise ValueError('新代码格式不正确。')
        code = _add_workspace_code(round_obj, actor,
            {**new_code, 'book_id': round_obj.book_id, 'revision': data.get('book_revision')},
            keep_existing_definition=True)
        data = data.copy()
        if data.get('no_code') is True:
            data['no_code'] = False
        if round_obj.book.policy == 'parallel':
            labels = data.get('labels', [])
            if not isinstance(labels, list):
                raise ValueError('代码格式不正确。')
            if code.pk not in labels:
                data['labels'] = [*labels, code.pk]
        else:
            data['primary'] = code.pk
            secondary = data.get('secondary')
            if (round_obj.book.policy == 'multi' and isinstance(secondary, list) and len(secondary) == 1
                    and _integer(secondary[0], '次代码') == code.pk):
                data['secondary'] = []
    values = validate_labels(round_obj.book, data, submitting=True)
    before = source_annotation_data(annotation) if annotation else {}
    if annotation is None:
        annotation = SourceAnnotation(task=task, start=start, end=end, text=text[start:end], region=region)
    for key, value in values.items():
        setattr(annotation, key, value)
    ids = ([annotation.primary_id] if annotation.primary_id else []) + annotation.secondary + annotation.labels
    annotation.book_snapshot = {'id': round_obj.book_id, 'version': round_obj.book.version,
        'title': round_obj.book.title, 'policy': round_obj.book.policy, 'revision': round_obj.book.revision,
        'source_text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'codes': [code_data(code) for code in round_obj.book.codes.filter(pk__in=ids)]}
    if region:
        annotation.book_snapshot['source_file_sha256'] = task.document.sha256
    annotation.revision += 1
    annotation.last_token = token
    annotation.save()
    task.save(update_fields=['updated_at'])
    audit(round_obj.project, actor, 'source.annotation.save', annotation,
          before=before, after=source_annotation_data(annotation))
    return annotation


@transaction.atomic
def set_source_completed(task_id, actor, data):
    reference = SourceCodingTask.objects.only('round_id').get(pk=task_id, coder=actor)
    round_obj = _lock_round(reference.round_id, actor)
    task = SourceCodingTask.objects.select_for_update().get(pk=task_id, coder=actor)
    if round_obj.state != 'active':
        raise Conflict('本轮已结束，材料状态只读保留。')
    if _integer(data.get('revision'), '材料版本') != task.revision:
        raise Conflict('材料状态已更新，请刷新后再试。')
    completed = data.get('completed')
    if type(completed) is not bool:
        raise ValueError('完成状态格式不正确。')
    before = task_data(task)
    task.completed = completed
    task.revision += 1
    task.save(update_fields=['completed', 'revision', 'updated_at'])
    audit(round_obj.project, actor, 'source.material.complete' if completed else 'source.material.reopen',
          task, before=before, after=task_data(task))
    return task


@transaction.atomic
def finish_source_coding(round_id, actor, revision):
    round_obj = _lock_round(round_id, actor)
    if round_obj.kind != 'quick' or round_obj.started_by_id != actor.pk or not round_obj.sampling.get('source_coding'):
        raise PermissionDenied
    if round_obj.state == 'closed':
        return round_obj
    if round_obj.revision != _integer(revision, '批次版本'):
        raise Conflict('批次状态已变化，请刷新后重试。')
    tasks = round_obj.source_tasks.all()
    if not tasks.exists() or tasks.filter(completed=False).exists():
        raise ValueError('请先确认本批所有材料已完成编码。')
    round_obj.state = 'closed'
    round_obj.revision += 1
    round_obj.save(update_fields=['state', 'revision'])
    audit(round_obj.project, actor, 'source.finish', round_obj,
          after={'state': round_obj.state, 'revision': round_obj.revision})
    return round_obj
