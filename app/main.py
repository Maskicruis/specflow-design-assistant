import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import store, pipeline, retrieval
from .ontology import STAGES
from .models import call_model, validate_endpoint, ModelError

store.init()
app=FastAPI(title='规序 SpecFlow · 规范辅助设计工作台',version='1.01')

@app.middleware('http')
async def local_guard(request:Request,call_next):
    host=request.headers.get('host','').split(':')[0]
    origin=request.headers.get('origin','')
    if host not in ('127.0.0.1','localhost','testserver'):
        return Response('Local access only',status_code=403)
    if request.method not in ('GET','HEAD','OPTIONS') and origin and urlparse(origin).netloc!=request.headers.get('host'):
        return Response('Cross-origin changes are blocked',status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
    return response

@app.get('/api/health')
def health():
    return {'status':'ok','app':'SpecFlow','version':'1.01','pid':os.getpid(),'instance':store.INSTANCE}

@app.get('/api/bootstrap')
def bootstrap():
    stats=store.stats()
    stages=[]
    for s in STAGES:
        stages.append({**s,'chunks':store.one('SELECT COUNT(*) n FROM chunks WHERE stages LIKE ?',('%"'+s['id']+'"%',))['n']})
    return {'stats':stats,'stages':stages,'models':store.public_settings(),'sources_dir':str(store.SOURCES),
       'recent':store.rows('SELECT id,question,stage,created FROM history ORDER BY created DESC LIMIT 6')}

@app.get('/api/documents')
def documents():
    return store.rows('''SELECT d.*,
      (SELECT COUNT(*) FROM pages p WHERE p.doc_id=d.id AND p.status IN ('indexed','empty')) AS "indexed",
      (SELECT COUNT(*) FROM pages p WHERE p.doc_id=d.id AND p.status IN ('pending_vision','failed')) pending,
      (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) chunks,
      (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id AND c.kind='table') tables
      FROM documents d ORDER BY d.created''')

def get_doc(id):
    doc=store.one('SELECT * FROM documents WHERE id=?',(id,))
    if not doc:
        raise HTTPException(404,'文档不存在')
    return doc

@app.get('/api/documents/{id}/original')
def original(id:str):
    doc=get_doc(id)
    path=store.resolve_source(doc['path'])
    if not path.is_file():
        raise HTTPException(404,'原文件缺失，请迁移规范文件和 data/imports 后重试')
    return FileResponse(path,media_type='application/pdf' if doc['kind']=='pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',filename=doc['name'],content_disposition_type='inline')

@app.get('/api/documents/{id}/cover')
def cover(id:str):
    get_doc(id)
    path=store.DATA/'previews'/f'{id}-cover.png'
    if not path.exists():
        raise HTTPException(404,'暂无封面')
    return FileResponse(path)

@app.get('/api/documents/{id}/pages')
def document_pages(id:str):
    get_doc(id)
    return store.rows('SELECT number,status,method,reviewed,substr(text,1,90) excerpt FROM pages WHERE doc_id=? ORDER BY number',(id,))

@app.get('/api/documents/{id}/pages/{number}')
def get_page(id:str,number:int):
    doc=get_doc(id)
    page=store.one('SELECT * FROM pages WHERE doc_id=? AND number=?',(id,number))
    if not page:
        raise HTTPException(404,'页面不存在')
    page['meta']=json.loads(page['meta'])
    page['blocks']=store.rows('SELECT * FROM blocks WHERE doc_id=? AND page=?',(id,number))
    for b in page['blocks']:
        b['detail']=json.loads(b['detail'])
        b['bbox']=json.loads(b['bbox'])
        b['detail'].pop('ooxml',None)
    return {'document':doc,'page':page}

@app.get('/api/documents/{id}/pages/{number}/image')
def image(id:str,number:int):
    try:
        return Response(pipeline.page_image(id,number,2),media_type='image/jpeg',headers={'Cache-Control':'private, max-age=3600'})
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404,str(exc))

@app.get('/api/assets/{name}')
def asset(name:str):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(png|jpe?g|gif|webp)',name):
        raise HTTPException(400,'仅可预览安全的位图格式')
    path=(store.DATA/'assets'/name).resolve()
    if path.parent!=(store.DATA/'assets').resolve() or not path.exists():
        raise HTTPException(404,'资源不存在')
    return FileResponse(path)

class ReviewRequest(BaseModel):
    reviewed:bool

@app.post('/api/documents/{id}/pages/{number}/review')
def review(id:str,number:int,body:ReviewRequest):
    page=store.one('SELECT status FROM pages WHERE doc_id=? AND number=?',(id,number))
    if not page or page['status'] not in ('indexed','empty'):
        raise HTTPException(400,'只有已完成解析的页面可标记复核')
    store.execute('UPDATE pages SET reviewed=? WHERE doc_id=? AND number=?',(int(body.reviewed),id,number))
    store.event(None,f'人工复核标记：{id} / {number} → {body.reviewed}')
    return {'ok':True}

@app.post('/api/import/folder')
def folder_import():
    files=sorted(p for p in store.SOURCES.rglob('*') if p.suffix.lower() in ('.pdf','.docx') and not p.name.startswith('~$'))
    return {'job_id':pipeline.enqueue_import(files),'files':len(files)}

@app.post('/api/import/upload')
async def upload(file:UploadFile=File(...)):
    name=Path((file.filename or '').replace('\\','/')).name
    suffix=Path(name).suffix.lower()
    if suffix not in ('.pdf','.docx'):
        raise HTTPException(400,'支持 PDF / DOCX；旧版 DOC 请另存为 DOCX')
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name)[:160]
    path=store.DATA/'imports'/f'{pipeline.uid()}_{name}'
    size=0
    try:
        with open(path,'wb') as f:
            while block:=await file.read(1024*1024):
                size+=len(block)
                if size>150*1024*1024:
                    raise HTTPException(413,'演示版单文件上限 150 MB')
                f.write(block)
        with open(path,'rb') as f:
            signature=f.read(5)
        if (suffix=='.pdf' and signature!=b'%PDF-') or (suffix=='.docx' and signature[:2]!=b'PK'):
            raise HTTPException(400,'文件内容与扩展名不一致')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'job_id':pipeline.enqueue_import([path]),'filename':name}

class VisionRequest(BaseModel):
    doc_id:str=''
    limit:int=Field(default=10,ge=1,le=400)

@app.post('/api/vision')
def vision(body:VisionRequest):
    cfg=store.settings()
    if not cfg['enabled']:
        raise HTTPException(400,'请先启用模型连接')
    try:
        return {'job_id':pipeline.enqueue_vision(body.doc_id,body.limit)}
    except ValueError as exc:
        raise HTTPException(409,str(exc))

@app.get('/api/jobs')
def jobs():
    return {'jobs':store.rows('SELECT * FROM jobs ORDER BY created DESC LIMIT 30'),
            'events':store.rows('SELECT * FROM events ORDER BY id DESC LIMIT 60')}

@app.post('/api/jobs/{id}/cancel')
def cancel(id:str):
    job=store.one('SELECT * FROM jobs WHERE id=?',(id,))
    if not job or job['kind']!='vision' or job['status'] not in ('queued','running'):
        raise HTTPException(400,'该任务不可停止')
    store.execute("UPDATE jobs SET status='cancel_requested',message='当前页面结束后停止' WHERE id=?",(id,))
    return {'ok':True}

class Query(BaseModel):
    question:str=Field(min_length=2,max_length=2000)
    stage:str=''
    doc_id:str=''
    use_model:bool=True

@app.post('/api/ask')
def ask(body:Query):
    return retrieval.answer(body.question,body.stage,body.doc_id,body.use_model)

@app.get('/api/search')
def search(q:str,stage:str='',doc_id:str='',kind:str=''):
    if not 1<len(q)<=2000:
        raise HTTPException(400,'请输入 2–2000 个字符')
    hits,ms=retrieval.search(q,stage,doc_id,kind,limit=30)
    return {'results':hits,'elapsed_ms':ms,'retriever':'Chinese BM25 + workflow/topic ranking'}

@app.get('/api/chunks/{id}')
def chunk(id:str):
    c=store.one('SELECT c.*,d.name,d.kind document_kind,d.draft FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id=?',(id,))
    if not c:
        raise HTTPException(404,'证据不存在')
    return retrieval.decode_chunk(c)

@app.get('/api/history/{id}')
def history(id:str):
    h=store.one('SELECT response FROM history WHERE id=?',(id,))
    if not h:
        raise HTTPException(404,'记录不存在')
    return json.loads(h['response'])

@app.get('/api/bookmarks')
def bookmarks():
    return [retrieval.decode_chunk(r) for r in store.rows('SELECT c.*,d.name,d.kind document_kind,d.draft FROM bookmarks b JOIN chunks c ON c.id=b.chunk_id JOIN documents d ON d.id=c.doc_id ORDER BY b.created DESC')]

@app.post('/api/bookmarks/{id}')
def toggle_bookmark(id:str):
    chunk(id)
    exists=store.one('SELECT * FROM bookmarks WHERE chunk_id=?',(id,))
    if exists:
        store.execute('DELETE FROM bookmarks WHERE chunk_id=?',(id,))
    else:
        store.execute('INSERT INTO bookmarks VALUES (?,?)',(id,store.now()))
    return {'saved':not bool(exists)}

@app.get('/api/settings')
def settings():
    return store.public_settings()

class Settings(BaseModel):
    enabled:bool
    base_url:str=Field(max_length=400)
    chat_model:str=Field(min_length=1,max_length=160)
    vision_model:str=Field(min_length=1,max_length=160)
    api_key_env:str=Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,99}$')
    allow_remote:bool=False

@app.post('/api/settings')
def update_settings(body:Settings):
    try:
        validate_endpoint(body.base_url)
    except ModelError as exc:
        raise HTTPException(400,str(exc))
    store.save_settings(body.model_dump())
    return store.public_settings()

@app.post('/api/settings/test')
def test_connection():
    try:
        content,_=call_model([{'role':'user','content':'请只回答：连接成功'}],max_tokens=30)
        return {'ok':True,'message':content[:100]}
    except ModelError as exc:
        raise HTTPException(400,str(exc))

app.mount('/',StaticFiles(directory=Path(__file__).parent/'static',html=True),name='ui')
