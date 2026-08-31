"""Resumable import: native PDF objects / OOXML, then vision-language interpretation."""
import hashlib
import os
import json
import re
import uuid
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import RLock
import pymupdf as fitz
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from . import store
from .ontology import CLAUSE, classify
from .models import interpret_image, ModelError

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='document-worker')
PDF_LOCK = RLock()

def uid():
    return uuid.uuid4().hex[:16]

def hash_file(path):
    digest = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def clean_text(text):
    text = text.replace('\x00','').replace('\r','')
    text = re.sub(r'(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])','',text)
    text = re.sub(r'[ \t]{2,}',' ',text)
    return re.sub(r'\n{3,}','\n\n',text).strip()

def table_text(rows):
    return '\n'.join(' | '.join('' if v is None else str(v) for v in row) for row in rows)

def add_block(db, doc, page, kind, text, bbox=None, detail=None, method='native'):
    block_id = uid()
    db.execute('INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)',
        (block_id,doc,page,kind,text,json.dumps(bbox),json.dumps(detail or {},ensure_ascii=False),method))
    return block_id

def add_chunk(db, doc, page, text, kind, clause='', heading='', method='native', detail=None):
    text = clean_text(text)
    if not text or (kind=='text' and len(text)<12):
        return
    stages, topics = classify(text)
    chunk_id=hashlib.sha256(f'{doc}|{page}|{kind}|{text}'.encode()).hexdigest()[:20]
    db.execute('INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (chunk_id,doc,page,page,clause,heading,text,kind,json.dumps(stages),json.dumps(topics,ensure_ascii=False),method,json.dumps(detail or {},ensure_ascii=False)))

def chunk_page(db, doc, page, text, method, heading='', previous_clause=''):
    text=clean_text(text)
    # The repeated running title is layout metadata, not clause evidence.
    text=re.sub(r'(?m)^变电工程总布置设计规程\s+DL/T[^\n]*\n?','',text).strip()
    text=re.sub(r'\n\s*\d{1,3}\s*$','',text).strip()
    matches = list(CLAUSE.finditer(text))
    segments = []
    if not matches:
        segments.append((previous_clause,text))
    else:
        prefix = text[:matches[0].start()].strip()
        if len(re.sub(r'\s','',prefix))>20 and re.search(r'[，。；：]',prefix):
            segments.append((previous_clause,prefix))
        for i,m in enumerate(matches):
            segments.append((m.group(1),text[m.start():matches[i+1].start() if i+1<len(matches) else len(text)]))
    for clause,segment in segments:
        # Keep clauses together; only long clauses use overlapping windows.
        for start in range(0,len(segment),1400):
            add_chunk(db,doc,page,segment[max(0,start-120):start+1400],'text',clause,heading,method)
    return matches[-1].group(1) if matches else previous_clause

def document_status(doc_id):
    pending = store.one("SELECT COUNT(*) n FROM pages WHERE doc_id=? AND status IN ('pending_vision','failed')",(doc_id,))['n']
    content = store.one('SELECT COUNT(*) n FROM chunks WHERE doc_id=?',(doc_id,))['n']
    status = ('partial' if content else 'awaiting_vision') if pending else 'ready'
    store.execute('UPDATE documents SET status=?,error=? WHERE id=?',(status,'',doc_id))

def reindex_document(doc_id):
    """Rebuild clause continuations in page order after asynchronous VLM work."""
    pages=store.rows('SELECT * FROM pages WHERE doc_id=? ORDER BY number',(doc_id,))
    heading=''
    previous_clause=''
    with store.connect() as db:
        saved=db.execute("SELECT c.page,c.text,b.created FROM bookmarks b JOIN chunks c ON c.id=b.chunk_id WHERE c.doc_id=? AND c.kind='text'",(doc_id,)).fetchall()
        db.execute("DELETE FROM bookmarks WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=? AND kind='text')",(doc_id,))
        db.execute("DELETE FROM chunks WHERE doc_id=? AND kind='text'",(doc_id,))
        for p in pages:
            if p['status'] not in ('indexed','empty') and p['method']!='native_ooxml':
                previous_clause=''
                continue
            text=clean_text(p['text'])
            if p['method']=='native_pdf':
                table_boxes=[json.loads(r['bbox']) for r in db.execute("SELECT bbox FROM blocks WHERE doc_id=? AND page=? AND kind='table' AND method='native_pdf'",(doc_id,p['number']))]
                if table_boxes:
                    doc_path=db.execute('SELECT path FROM documents WHERE id=?',(doc_id,)).fetchone()['path']
                    with PDF_LOCK,fitz.open(store.resolve_source(doc_path)) as pdf:
                        layout=pdf[p['number']-1].get_text('dict',flags=fitz.TEXTFLAGS_TEXT,sort=True)
                        lines=[]
                        for b in layout['blocks']:
                            for line in b.get('lines',[]):
                                spans=[]
                                for span in line['spans']:
                                    box=fitz.Rect(span['bbox'])
                                    if not any((box & fitz.Rect(t)).get_area()>box.get_area()*.45 for t in table_boxes if t):
                                        spans.append(span['text'])
                                if spans:
                                    lines.append(''.join(spans))
                        text=clean_text('\n'.join(lines))
            if re.search(r'(?m)^\s*条文说明\s*$',text) and len(text)<650:
                heading='条文说明（解释性材料）'
                previous_clause=''
            if p['method']=='vlm':
                blocks=db.execute("SELECT text,detail FROM blocks WHERE doc_id=? AND page=? AND method='vlm' AND kind IN ('paragraph','heading') ORDER BY rowid",(doc_id,p['number'])).fetchall()
                texts=[]
                for b in blocks:
                    detail=json.loads(b['detail'])
                    if detail.get('uncertain') or '[不可辨认]' in b['text']:
                        continue
                    content=b['text']
                    texts.append(content)
                text='\n'.join(texts)
            previous_clause=chunk_page(db,doc_id,p['number'],text,p['method'],heading,previous_clause)
            meta=json.loads(p['meta'])
            meta['section_type']='commentary' if heading else 'body_or_frontmatter'
            db.execute('UPDATE pages SET meta=? WHERE doc_id=? AND number=?',(json.dumps(meta,ensure_ascii=False),doc_id,p['number']))
            if heading:
                db.execute("UPDATE chunks SET heading=? || CASE WHEN heading='' THEN '' ELSE ' · '||heading END WHERE doc_id=? AND page=? AND kind!='text' AND heading NOT LIKE '条文说明%'",(heading,doc_id,p['number']))
        for bookmark in saved:
            found=db.execute("SELECT id FROM chunks WHERE doc_id=? AND page=? AND text=? AND kind='text'",(doc_id,bookmark['page'],bookmark['text'])).fetchone()
            if found:
                db.execute('INSERT OR IGNORE INTO bookmarks VALUES (?,?)',(found['id'],bookmark['created']))

def import_file(path, job_id=None):
    path = Path(path).resolve()
    if path.suffix.lower() not in ('.pdf','.docx'):
        raise ValueError('支持 PDF / DOCX；旧版 .doc 请在 Word 中另存为 DOCX')
    checksum = hash_file(path)
    existing = store.one('SELECT * FROM documents WHERE hash=?',(checksum,))
    if existing:
        store.execute('UPDATE documents SET path=? WHERE id=?',(store.source_locator(path),existing['id']))
        # A failed import can be retried without duplicating document identity.
        if existing['status'] != 'failed':
            return {'id':existing['id'],'duplicate':True}
        doc_id = existing['id']
        with store.connect() as db:
            for table in ('blocks','chunks','pages'):
                if table=='chunks':
                    db.execute('DELETE FROM bookmarks WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)',(doc_id,))
                db.execute(f'DELETE FROM {table} WHERE doc_id=?',(doc_id,))
            db.execute("UPDATE documents SET status='processing',error='' WHERE id=?",(doc_id,))
    else:
        doc_id = checksum[:16]
        store.execute('INSERT INTO documents(id,name,path,hash,kind,status,draft,created) VALUES (?,?,?,?,?,?,?,?)',
           (doc_id,path.name,store.source_locator(path),checksum,path.suffix[1:].lower(),'processing',int(any(x in path.name for x in ['报批稿','征求意见','草案'])),store.now()))
    try:
        if path.suffix.lower()=='.pdf':
            with PDF_LOCK:
                import_pdf(doc_id,path,job_id)
        else:
            import_docx(doc_id,path)
        document_status(doc_id)
        return {'id':doc_id,'duplicate':False}
    except Exception as exc:
        store.execute("UPDATE documents SET status='failed',error=? WHERE id=?",(str(exc)[:300],doc_id))
        raise

def import_pdf(doc_id,path,job):
    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError('PDF 已加密，请先提供可读取的副本')
        if len(pdf)>2000:
            raise ValueError('演示版单文档限制为 2000 页')
        store.execute('UPDATE documents SET pages=? WHERE id=?',(len(pdf),doc_id))
        previous_clause = ''
        heading = ''
        for i,page in enumerate(pdf):
            number = i+1
            text = clean_text(page.get_text('text',sort=True))
            chars = len(re.sub(r'\s','',text))
            images = page.get_image_info()
            drawable = page.get_drawings()
            valid_ratio = sum(c.isalnum() for c in text)/max(len(text),1)
            # Do not treat repeated watermark text on a scan as a usable text layer.
            image_area = max((fitz.Rect(x['bbox']).get_area() for x in images),default=0)
            large_image = image_area > page.rect.get_area()*0.65
            native = chars>=60 and valid_ratio>0.25 and text.count('\ufffd')<max(5,chars*.03) and not (large_image and chars<180)
            is_empty = not native and not images and not drawable and chars==0
            status = 'indexed' if native else ('empty' if is_empty else 'pending_vision')
            meta = {'image_count':len(images),'vector_count':len(drawable),'label':page.get_label(),
                    'visual_pending': bool(native and images), 'warnings':[]}
            tables=[]
            if native:
                try:
                    for t in page.find_tables(use_layout=False).tables:
                        rows=t.extract()
                        if len(rows)>=2 and any(len(r)>1 for r in rows):
                            tables.append({'rows':rows,'bbox':list(t.bbox),'cells':t.cells,'header':t.header.names})
                except Exception as exc:
                    meta['warnings'].append('原生表格检测未完成：'+type(exc).__name__)
            with store.connect() as db:
                db.execute('INSERT INTO pages(doc_id,number,method,status,text,width,height,meta) VALUES (?,?,?,?,?,?,?,?)',
                  (doc_id,number,'native_pdf' if native else 'vision_required',status,text if native else '',page.rect.width,page.rect.height,json.dumps(meta,ensure_ascii=False)))
                if native:
                    layout=page.get_text('dict',flags=fitz.TEXTFLAGS_TEXT,sort=True)
                    for b in layout['blocks']:
                        if b.get('type')!=0:
                            continue
                        bt=clean_text('\n'.join(''.join(s['text'] for s in line['spans']) for line in b.get('lines',[])))
                        styles=[{'font':s['font'],'size':round(s['size'],2),'flags':s['flags'],'text':s['text']} for line in b.get('lines',[]) for s in line['spans']]
                        add_block(db,doc_id,number,'paragraph',bt,b['bbox'],{'spans':styles},'native_pdf')
                    if re.search(r'(?m)^\s*条文说明\s*$',text) and len(text)<650:
                        heading='条文说明（解释性材料）'
                        previous_clause=''
                    previous_clause=chunk_page(db,doc_id,number,text,'native_pdf',heading,previous_clause)
                    for t in tables:
                        add_block(db,doc_id,number,'table',table_text(t['rows']),t['bbox'],t,'native_pdf')
                        add_chunk(db,doc_id,number,table_text(t['rows']),'table',heading=heading,method='native_pdf',detail=t)
                for im in images:
                    add_block(db,doc_id,number,'figure','整页扫描图，等待视觉理解' if not native else '嵌入图像，语义待视觉理解',list(im['bbox']),
                              {'width':im['width'],'height':im['height'],'pending':True},'visual_asset')
            if number==1:
                page.get_pixmap(matrix=fitz.Matrix(.55,.55),alpha=False).save(store.DATA/'previews'/f'{doc_id}-cover.png')
            if job and (number%10==0 or number==len(pdf)):
                store.execute('UPDATE jobs SET message=?,updated=? WHERE id=?',(f'{path.name} · 已检查 {number}/{len(pdf)} 页',store.now(),job))

def import_docx(doc_id,path):
    with zipfile.ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist())>800*1024*1024:
            raise ValueError('DOCX 解压后超过演示版 800 MB 安全限制')
    document=Document(path)
    heading=''
    previous_clause=''
    number=0
    for element in document.element.body:
        if element.tag not in (qn('w:p'),qn('w:tbl')):
            continue
        number+=1
        blocks=[]
        text=''
        if element.tag==qn('w:p'):
            p=Paragraph(element,document)
            text=p.text
            if p.style and ('Heading' in p.style.name or '标题' in p.style.name):
                heading=text
            blocks.append(('paragraph',text,{'style':p.style.name if p.style else '',
                          'runs':[{'text':r.text,'bold':r.bold,'italic':r.italic,'font':r.font.name,'size':r.font.size.pt if r.font.size else None} for r in p.runs]}))
        else:
            t=Table(element,document)
            rows=[[c.text for c in row.cells] for row in t.rows]
            # OOXML retains gridSpan/vMerge exactly, including complex merged cells.
            blocks.append(('table',table_text(rows),{'rows':rows,'ooxml':element.xml}))
            text=table_text(rows)
        for blip in element.iter(qn('a:blip')):
            rid=blip.get(qn('r:embed'))
            if not rid or rid not in document.part.related_parts:
                continue
            part=document.part.related_parts[rid]
            extension=Path(str(part.partname)).suffix.lower()
            name=f'{doc_id}-{number}-{uid()}{extension}'
            (store.DATA/'assets'/name).write_bytes(part.blob)
            blocks.append(('figure','Word 内嵌图片，等待视觉理解',{'asset':name,'mime':part.content_type,'pending':True}))
        pending=any(k=='figure' for k,_,_ in blocks)
        with store.connect() as db:
            db.execute('INSERT INTO pages(doc_id,number,method,status,text,width,height,meta) VALUES (?,?,?,?,?,?,?,?)',
              (doc_id,number,'native_ooxml','pending_vision' if pending else ('indexed' if text.strip() else 'empty'),text,0,0,json.dumps({'anchor':f'正文块 {number}','heading':heading},ensure_ascii=False)))
            for kind,bt,detail in blocks:
                add_block(db,doc_id,number,kind,bt,None,detail,'native_ooxml')
                if kind=='table':
                    add_chunk(db,doc_id,number,bt,'table',heading=heading,method='native_ooxml',detail=detail)
            if element.tag==qn('w:p'):
                previous_clause=chunk_page(db,doc_id,number,text,'native_ooxml',heading,previous_clause)
    store.execute('UPDATE documents SET pages=? WHERE id=?',(number,doc_id))

def page_image(doc_id,number,scale=1.65):
    doc=store.one('SELECT * FROM documents WHERE id=?',(doc_id,))
    if not doc or doc['kind']!='pdf' or number<1 or number>doc['pages']:
        raise ValueError('PDF 页码无效')
    with PDF_LOCK, fitz.open(store.resolve_source(doc['path'])) as pdf:
        page=pdf[number-1]
        # Bound model request image size; full original always stays accessible.
        scale=min(scale,2200/max(page.rect.width,page.rect.height))
        return page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg',jpg_quality=90)

def interpret_page(doc_id,number,config=None):
    doc=store.one('SELECT * FROM documents WHERE id=?',(doc_id,))
    page=store.one('SELECT * FROM pages WHERE doc_id=? AND number=?',(doc_id,number))
    if not doc or not page:
        raise ValueError('页面不存在')
    if doc['kind']=='pdf':
        result=interpret_image(page_image(doc_id,number),config=config)
        results=[result]
    else:
        assets=store.rows("SELECT detail FROM blocks WHERE doc_id=? AND page=? AND kind='figure' AND method='native_ooxml'",(doc_id,number))
        results=[]
        for item in assets:
            detail=json.loads(item['detail'])
            if detail['mime'] not in ('image/png','image/jpeg','image/webp','image/gif'):
                raise ModelError('此 Word 图片格式需转换为 PNG/JPEG 后再进行视觉理解')
            results.append(interpret_image((store.DATA/'assets'/detail['asset']).read_bytes(),detail['mime'],config))
    texts=[]
    warnings=[]
    all_blocks=[b for result in results for b in result['blocks']]
    if not all_blocks and any(result.get('warnings') for result in results):
        raise ModelError('未提取到内容且模型报告页面问题，保留待处理状态')
    with store.connect() as db:
        db.execute("DELETE FROM bookmarks WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=? AND page=? AND method='vlm')",(doc_id,number))
        db.execute("DELETE FROM chunks WHERE doc_id=? AND page=? AND method='vlm'",(doc_id,number))
        db.execute("DELETE FROM blocks WHERE doc_id=? AND page=? AND method='vlm'",(doc_id,number))
        for b in all_blocks:
            bt=b.get('text','')
            if b['type']=='table':
                bt='\n'.join(x for x in [b.get('caption',''),bt,table_text(b.get('rows',[])),b.get('notes','')] if x)
            elif b.get('notes'):
                bt+='\n'+b['notes']
            box=b.get('bbox')
            absolute=[box[0]*page['width'],box[1]*page['height'],box[2]*page['width'],box[3]*page['height']] if box else None
            add_block(db,doc_id,number,b['type'],bt,absolute,b,'vlm')
            if b.get('uncertain') or '[不可辨认]' in bt:
                warnings.append('存在不确定块：'+bt[:100])
                # Uncertain numerical requirements must not enter answer retrieval.
                continue
            texts.append(bt)
            if b['type'] in ('table','figure'):
                add_chunk(db,doc_id,number,bt,b['type'],b.get('clause',''),b.get('caption',''),'vlm',b)
        body='\n'.join(b.get('text','') for b in all_blocks if b['type'] in ('paragraph','heading') and not b.get('uncertain') and '[不可辨认]' not in b.get('text',''))
        chunk_page(db,doc_id,number,body,'vlm')
        meta=json.loads(page['meta'])
        warnings.extend(w for result in results for w in result.get('warnings',[]))
        meta.update({'warnings':warnings,'vision_model':(config or store.settings())['vision_model'],
                     'page_label':results[0].get('page_label','') if results else '',
                     'usage':[r.get('usage',{}) for r in results], 'visual_pending':False})
        combined=('' if doc['kind']=='pdf' else page['text']+'\n')+'\n'.join(texts)
        db.execute('UPDATE pages SET method=?,status=?,text=?,meta=?,reviewed=0 WHERE doc_id=? AND number=?',
                   ('vlm' if doc['kind']=='pdf' else 'ooxml+vlm','indexed' if all_blocks else 'empty',combined,json.dumps(meta,ensure_ascii=False),doc_id,number))
    document_status(doc_id)
    return {'blocks':len(all_blocks),'warnings':warnings}

def new_job(kind,total=0):
    job=uid()
    store.execute('INSERT INTO jobs(id,kind,status,total,done,failed,message,created,updated,owner_pid) VALUES (?,?,?,?,?,?,?,?,?,?)',(job,kind,'queued',total,0,0,'等待处理',store.now(),store.now(),os.getpid()))
    return job

def run_import(paths,job):
    store.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
    for path in paths:
        try:
            result=import_file(path,job)
            store.event(job,Path(path).name+(' · 已存在，跳过重复导入' if result['duplicate'] else ' · 结构已入库'))
            store.execute('UPDATE jobs SET done=done+1,updated=? WHERE id=?',(store.now(),job))
        except Exception as exc:
            store.event(job,Path(path).name+' · '+str(exc)[:300],'error')
            store.execute('UPDATE jobs SET done=done+1,failed=failed+1,updated=? WHERE id=?',(store.now(),job))
    failed=store.one('SELECT failed FROM jobs WHERE id=?',(job,))['failed']
    store.execute('UPDATE jobs SET status=?,message=?,updated=? WHERE id=?',('completed_with_errors' if failed else 'completed','导入结束；扫描页面需继续视觉理解',store.now(),job))

def enqueue_import(paths):
    job=new_job('import',len(paths))
    EXECUTOR.submit(run_import,paths,job)
    return job

def run_vision(pages,job):
    store.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
    config=store.settings()
    consecutive_failures=0
    stop_reason=''
    iterator=iter(pages)
    # Three bounded network calls; PDF rendering is serialized by PDF_LOCK.
    with ThreadPoolExecutor(max_workers=3) as pool:
        active={}
        def submit_next():
            page=next(iterator,None)
            if page:
                active[pool.submit(interpret_page,page['doc_id'],page['number'],store.settings())]=page
        for _ in range(3):
            submit_next()
        while active:
            done,_=wait(active,timeout=1,return_when=FIRST_COMPLETED)
            if store.one('SELECT status FROM jobs WHERE id=?',(job,))['status']=='cancel_requested':
                stop_reason='cancelled'
            if not store.settings()['enabled']:
                stop_reason='cancelled'
            for future in done:
                page=active.pop(future)
                doc=store.one('SELECT name FROM documents WHERE id=?',(page['doc_id'],))
                label=f"{doc['name']} · {page['number']}"
                try:
                    result=future.result()
                    store.event(job,f'{label} · {result["blocks"]} 个结构块'+(' · 存在需复核项' if result['warnings'] else ''))
                    consecutive_failures=0
                except Exception as exc:
                    consecutive_failures+=1
                    store.execute("UPDATE pages SET status='failed' WHERE doc_id=? AND number=?",(page['doc_id'],page['number']))
                    store.execute('UPDATE jobs SET failed=failed+1 WHERE id=?',(job,))
                    store.event(job,label+' · '+str(exc)[:240],'error')
                store.execute('UPDATE jobs SET done=done+1,message=?,updated=? WHERE id=?',(label,store.now(),job))
                if consecutive_failures>=3:
                    stop_reason='failed'
                if not stop_reason:
                    submit_next()
    if stop_reason:
        store.execute('UPDATE jobs SET status=?,message=?,updated=? WHERE id=?',(stop_reason,'已停止；成功页面已保存。若连续失败，请修复模型连接后重试。',store.now(),job))
        return
    failed=store.one('SELECT failed FROM jobs WHERE id=?',(job,))['failed']
    for doc_id in {p['doc_id'] for p in pages}:
        reindex_document(doc_id)
    store.execute('UPDATE jobs SET status=?,message=?,updated=? WHERE id=?',('completed_with_errors' if failed else 'completed','视觉理解任务结束，机器解析结果请复核原文',store.now(),job))

def enqueue_vision(doc_id='',limit=10):
    busy=store.one("SELECT id FROM jobs WHERE kind='vision' AND status IN ('queued','running','cancel_requested')")
    if busy:
        raise ValueError('已有视觉任务进行中，请等待完成或停止后再创建')
    query="SELECT doc_id,number FROM pages WHERE status IN ('pending_vision','failed')"
    args=[]
    if doc_id:
        query+=' AND doc_id=?'
        args.append(doc_id)
    query+=' ORDER BY doc_id,number LIMIT ?'
    args.append(limit)
    pages=store.rows(query,args)
    if not pages:
        raise ValueError('没有待处理页面')
    job=new_job('vision',len(pages))
    EXECUTOR.submit(run_vision,pages,job)
    return job
