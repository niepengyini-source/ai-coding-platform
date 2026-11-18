"""Common JSON representation; original bytes and legacy row semantics stay intact."""
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import fromstring as safe_fromstring
from defusedxml.common import DefusedXmlException

from .importing import DOCUMENT_FORMATS, IMAGE_FORMATS, MAX_BYTES, MAX_DOCUMENT_BYTES, MAX_ROWS

MAX_DOCUMENT_CHARS = 2 * 1024 * 1024


def _source(filename, raw, sheet_name=''):
    return {'filename': Path(filename).name, 'format': Path(filename).suffix.lower().lstrip('.'),
            'sha256': hashlib.sha256(bytes(raw)).hexdigest(), 'sheet': sheet_name}


def normalize_records(filename, raw, rows, text_column, sheet_name=''):
    headers = list(rows[0]) if rows else [text_column]
    names = {''.join(character for character in key.casefold() if character.isalnum()) for key in headers}
    conversation = {'speaker', 'sender', 'senderid', 'sendername', 'senderusername', 'role', 'timestamp',
                    'time', '发言者', '说话人', '发送者', '发言时间', '时间', '身份', '角色'}
    texts = [row.get(text_column, '') for row in rows if row.get(text_column, '').strip()]
    # A reading recommendation only: long prose never changes row identities or creates different tasks.
    long_prose = (sum(map(len, texts)) >= 2000 and texts and
                  sum(len(text) >= 400 for text in texts) * 2 >= len(texts))
    presentation = 'document' if not names.intersection(conversation) and long_prose else 'unit'
    return {'schema_version': 1, 'type': 'records', 'presentation': presentation,
            'source': _source(filename, raw, sheet_name), 'headers': headers, 'rows': rows,
            'blocks': [{'id': str(position), 'text': row.get(text_column, ''), 'start': 0,
                        'end': len(row.get(text_column, '')), 'row': position,
                        'metadata': {key: value for key, value in row.items() if key != text_column}}
                       for position, row in enumerate(rows, 1)], 'warnings': []}


def _pdf_pages(raw):
    try:
        worker = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'coding.pdf_extract', '--material'],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
            cwd=Path(__file__).resolve().parent.parent)
        result = json.loads(worker.stdout.decode('utf-8'))
    except subprocess.TimeoutExpired as error:
        raise ValueError('PDF解析超时，请拆分文档或提供可复制文字的版本。') from error
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError('PDF无法安全读取，请检查文件或提供文字版；没有导入部分内容。') from error
    if not isinstance(result, dict):
        raise ValueError('PDF解析结果不完整，请重新提供可读取文字的文件。')
    if worker.returncode or result.get('error'):
        raise ValueError(result.get('error', 'PDF读取失败，没有导入部分内容。'))
    return result['pages']


def _paragraph_text(paragraph, ns):
    return ''.join((node.text or '') if node.tag == ns + 't' else
                   '\n' if node.tag in (ns + 'br', ns + 'cr') else
                   '\t' if node.tag == ns + 'tab' else '' for node in paragraph.iter())


def _docx_parts(raw):
    # Reuse the existing bounded Office parser instead of adding a second ZIP trust boundary.
    from .codebook_files import inspect_office_zip
    inspect_office_zip(raw)
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            document = safe_fromstring(archive.read('word/document.xml'), forbid_dtd=True,
                                       forbid_entities=True, forbid_external=True)
            body = document.find(ns + 'body')
            if body is None:
                raise ValueError('Word文档缺少正文。')
            parts = []
            def body_nodes(parent):
                for node in parent:
                    if node.tag in (ns + 'sdt', ns + 'sdtContent', ns + 'customXml', ns + 'ins'):
                        yield from body_nodes(node)
                    else:
                        yield node
            for node in body_nodes(body):
                if node.tag == ns + 'p':
                    text = _paragraph_text(node, ns)
                    style = node.find(ns + 'pPr/' + ns + 'pStyle')
                    style_id = style.get(ns + 'val', '') if style is not None else ''
                    match = re.fullmatch(r'(?:Heading|标题)([1-9])', style_id, re.I)
                    level = int(match[1]) if match else 0
                    parts.append({'text': text, 'kind': 'heading' if level else 'paragraph', 'level': level})
                elif node.tag == ns + 'tbl':
                    for row in node.findall(ns + 'tr'):
                        cells = ['\n'.join(_paragraph_text(p, ns) for p in cell.iter(ns + 'p'))
                                 for cell in row.findall(ns + 'tc')]
                        parts.append({'text': '\t'.join(cells), 'kind': 'table', 'level': 0})
            for name, kind in [('word/footnotes.xml', 'footnote'), ('word/endnotes.xml', 'endnote')]:
                if name not in archive.namelist():
                    continue
                notes = safe_fromstring(archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)
                for note in notes:
                    if note.get(ns + 'type') in ('separator', 'continuationSeparator'):
                        continue
                    text = '\n'.join(_paragraph_text(p, ns) for p in note.findall(ns + 'p'))
                    if text.strip():
                        parts.append({'text': text, 'kind': kind, 'level': 0})
            if any(document.find('.//' + ns + name) is not None for name in ('drawing', 'pict', 'object', 'altChunk')):
                raise ValueError('Word包含图片或绘图，当前无法确认其中是否有正文。请先完成OCR或提供完整文字版后导入。')
            return parts
    except (zipfile.BadZipFile, KeyError, ParseError, DefusedXmlException, OSError) as error:
        raise ValueError('Word文件损坏或格式不受支持，请另存为普通DOCX。') from error


def normalize_material(filename, raw, sheet_name=''):
    """Convert supported source types, without claiming that JSON itself performs OCR."""
    suffix = Path(filename).suffix.lower()
    if not raw or len(raw) > (MAX_DOCUMENT_BYTES if suffix in DOCUMENT_FORMATS else MAX_BYTES):
        raise ValueError('请选择非空文件，PDF、Word和图片最大80MB，表格和文本最大8MB。')
    if suffix in IMAGE_FORMATS:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw), formats=[IMAGE_FORMATS[suffix]]) as image:
                width, height = image.size
                if max(width, height) > 12000 or width * height > 20_000_000:
                    raise ValueError('图片最多2000万像素，单边最长12000像素。')
                if getattr(image, 'is_animated', False):
                    raise ValueError('请将动画图片另存为单帧 PNG 后导入。')
                if image.getexif().get(274, 1) in (5, 6, 7, 8):
                    width, height = height, width
                image.load()
        except Image.DecompressionBombError as error:
            raise ValueError('图片尺寸超出限制。') from error
        except (OSError, SyntaxError) as error:
            raise ValueError('图片无法读取，请确认文件格式与扩展名一致。') from error
        return {'schema_version': 1, 'type': 'image', 'presentation': 'image',
                'source': _source(filename, raw), 'image': {'width': width, 'height': height},
                'text': '', 'blocks': [], 'warnings': []}
    if suffix not in DOCUMENT_FORMATS:
        from .importing import parse_upload, suggested_columns
        headers, rows = parse_upload(filename, raw, sheet_name or None)
        text_column, _ = suggested_columns(headers)
        return normalize_records(filename, raw, rows, text_column or headers[0], sheet_name)
    warnings = ['阅读文本用于编码定位，不保证与原件版式相同；请核对正文、表格、页眉和脚注。']
    pages = _pdf_pages(raw) if suffix == '.pdf' else []
    page_map = []
    pages_to_review = []
    if suffix == '.pdf':
        parts = []
        for page in pages:
            details = {'page': page['page'], 'empty': not page['text'].strip()}
            if page.get('review'):
                details['review'] = page['review']
                pages_to_review.append(page['page'])
            page_map.append(details)
            for paragraph in re.split(r'\n\s*\n', page['text']):
                parts.append({'text': paragraph, 'page': page['page'], 'kind': 'paragraph', 'level': 0})
    else:
        parts = _docx_parts(raw)
        warnings.append('Word按正文顺序读取，表格以行和制表符呈现，脚注附于正文后；不推测Word页码。')
    blocks, chunks, offset, heading = [], [], 0, ''
    for part in parts:
        text = part['text'].replace('\r\n', '\n').replace('\r', '\n').strip()
        if not text:
            continue
        if '\x00' in text:
            raise ValueError('提取文字包含无效空字符，请核对源文件。')
        if blocks:
            chunks.append('\n\n')
            offset += 2
        level = part.get('level', 0)
        if level:
            heading = text
        blocks.append({'id': str(len(blocks) + 1), 'text': text, 'start': offset,
                       'end': offset + len(text), 'page': part.get('page'),
                       'heading': heading, 'level': level, 'kind': part['kind']})
        chunks.append(text)
        offset += len(text)
        if offset > MAX_DOCUMENT_CHARS or len(blocks) > MAX_ROWS:
            raise ValueError('提取文档超过200万字或10000个段落，请按章节拆分导入。')
    if not blocks and not pages_to_review:
        raise ValueError('没有可读取的正文，请完成OCR或提供非空文字版。')
    return {'schema_version': 1, 'type': 'document', 'presentation': 'document',
            'source': _source(filename, raw), 'text': ''.join(chunks), 'blocks': blocks,
            'pages': page_map, 'pages_to_review': pages_to_review, 'warnings': warnings}


def document_payload(document):
    """Read old materials through the same representation without rewriting their data."""
    if document.normalized:
        return document.normalized
    records = list(document.records.order_by('position'))
    rows = [{document.text_column: record.text, **record.metadata} for record in records]
    result = normalize_records(document.name, bytes(document.raw), rows, document.text_column, document.sheet_name)
    for block, record in zip(result['blocks'], records):
        block['id'] = str(record.position)
        block['row'] = record.position
    return result
