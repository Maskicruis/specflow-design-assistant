"""Local, transactional storage. Originals are never modified."""
import json
import os
import sqlite3
import hashlib
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

from .config import ROOT, DATA, SOURCES, INSTANCE

DATA.mkdir(parents=True, exist_ok=True)
SOURCES.mkdir(parents=True, exist_ok=True)
for folder in ('previews', 'assets', 'imports'):
    (DATA / folder).mkdir(exist_ok=True)
DB = DATA / 'knowledge.sqlite3'

def source_locator(path):
    """Managed files use root-relative locators; no drive letter is persisted."""
    path = Path(path).resolve()
    for prefix, root in (('sources', SOURCES), ('data', DATA)):
        try:
            return prefix + ':' + path.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return str(path)

def resolve_source(locator):
    for prefix, root in (('sources:', SOURCES), ('data:', DATA)):
        if locator.startswith(prefix):
            root = root.resolve()
            suffix = locator[len(prefix):].replace('\\', '/')
            path = (root / suffix).resolve()
            if not path.is_relative_to(root):
                raise ValueError('原文路径超出资料目录')
            return path
    return Path(locator)

def migrate_source_paths():
    """Upgrade legacy absolute paths only after a matching SHA-256 is found."""
    for doc in rows('SELECT id,path,name,hash FROM documents'):
        locator = doc['path']
        if locator.startswith(('sources:', 'data:')):
            continue
        candidates = []
        raw = locator.replace('\\', '/')
        for marker, root in (('/规范文件/', SOURCES), ('/data/', DATA)):
            if marker in raw:
                candidate = (root / raw.split(marker, 1)[1]).resolve()
                if candidate.is_relative_to(root.resolve()):
                    candidates.append(candidate)
        # The name fallback also handles custom roots from an older installation.
        name = Path(raw).name
        candidates.extend((SOURCES / name, DATA / 'imports' / name, Path(locator)))
        for candidate in dict.fromkeys(candidates):
            try:
                if not candidate.is_file():
                    continue
                with candidate.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                if checksum != doc['hash']:
                    continue
                updated = source_locator(candidate)
                if updated != locator:
                    execute('UPDATE documents SET path=? WHERE id=?', (updated, doc['id']))
                break
            except OSError:
                logging.getLogger(__name__).warning('Cannot verify original for document %s', doc['id'])

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init():
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, hash TEXT UNIQUE NOT NULL,
          kind TEXT NOT NULL, pages INTEGER DEFAULT 0, status TEXT DEFAULT 'queued',
          edition TEXT DEFAULT '用户提供版本 · 效力待核验', draft INTEGER DEFAULT 0,
          created TEXT NOT NULL, error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS pages (
          doc_id TEXT REFERENCES documents(id), number INTEGER, method TEXT,
          status TEXT, text TEXT DEFAULT '', width REAL, height REAL, meta TEXT DEFAULT '{}',
          reviewed INTEGER DEFAULT 0, PRIMARY KEY(doc_id,number));
        CREATE TABLE IF NOT EXISTS blocks (
          id TEXT PRIMARY KEY, doc_id TEXT REFERENCES documents(id), page INTEGER,
          kind TEXT, text TEXT, bbox TEXT, detail TEXT, method TEXT);
        CREATE TABLE IF NOT EXISTS chunks (
          id TEXT PRIMARY KEY, doc_id TEXT REFERENCES documents(id), page INTEGER,
          page_end INTEGER, clause TEXT, heading TEXT, text TEXT, kind TEXT,
          stages TEXT, topics TEXT, method TEXT, detail TEXT DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS chunks_document ON chunks(doc_id,page);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, kind TEXT, status TEXT, total INTEGER DEFAULT 0,
          done INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, message TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, job_id TEXT, level TEXT, message TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS history (
          id TEXT PRIMARY KEY, question TEXT, stage TEXT, response TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS bookmarks (
          chunk_id TEXT PRIMARY KEY REFERENCES chunks(id), created TEXT);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value INTEGER);
        INSERT OR IGNORE INTO metadata VALUES ('corpus_revision',0);
        CREATE TRIGGER IF NOT EXISTS chunk_insert_revision AFTER INSERT ON chunks BEGIN
          UPDATE metadata SET value=value+1 WHERE key='corpus_revision'; END;
        CREATE TRIGGER IF NOT EXISTS chunk_delete_revision AFTER DELETE ON chunks BEGIN
          UPDATE metadata SET value=value+1 WHERE key='corpus_revision'; END;
        CREATE TRIGGER IF NOT EXISTS chunk_update_revision AFTER UPDATE ON chunks BEGIN
          UPDATE metadata SET value=value+1 WHERE key='corpus_revision'; END;
        ''')
        if 'owner_pid' not in [r[1] for r in db.execute('PRAGMA table_info(jobs)')]:
            db.execute('ALTER TABLE jobs ADD COLUMN owner_pid INTEGER')
        for job in db.execute("SELECT id,owner_pid FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchall():
            if not process_alive(job['owner_pid']):
                db.execute("UPDATE jobs SET status='interrupted',message='服务重启，任务中断；已保存页面可继续处理' WHERE id=?",(job['id'],))
    migrate_source_paths()

def process_alive(pid):
    if not pid:
        return False
    if os.name=='nt':
        import ctypes
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
        if not handle:
            return False
        code=ctypes.c_ulong()
        ok=ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value==259
    try:
        os.kill(pid,0)
        return True
    except OSError:
        return False

def rows(sql, args=()):
    with connect() as db:
        return [dict(x) for x in db.execute(sql, args).fetchall()]

def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None

def execute(sql, args=()):
    with connect() as db:
        db.execute(sql, args)

def event(job, message, level='info'):
    execute('INSERT INTO events(job_id,level,message,created) VALUES (?,?,?,?)', (job, level, message, now()))

DEFAULTS = {
    'enabled': False, 'base_url': 'https://api.deepseek.com',
    'chat_model': 'deepseek-v4-flash', 'vision_model': 'deepseek-v4-flash-vision-exp',
    'api_key_env': 'DEEPSEEK_API_KEY', 'allow_remote': False,
    'embedding_url': '', 'embedding_model': '', 'embedding_key_env': '',
}

def settings():
    return {**DEFAULTS, **{r['key']: json.loads(r['value']) for r in rows('SELECT * FROM settings')}}

def public_settings():
    s = settings()
    s['key_present'] = bool(os.environ.get(s['api_key_env'], ''))
    s['remote_note'] = '启用远程模型后，问题、检索片段及待解析页面将发送到所填服务；API 可能计费。密钥仅从服务器环境变量读取。'
    return s

def save_settings(values):
    with connect() as db:
        for key, value in values.items():
            if key in DEFAULTS:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

def stats():
    return {
      'documents': one('SELECT COUNT(*) n FROM documents')['n'],
      'pages': one('SELECT COUNT(*) n FROM pages')['n'],
      'indexed_pages': one("SELECT COUNT(*) n FROM pages WHERE status IN ('indexed','empty')")['n'],
      'pending_pages': one("SELECT COUNT(*) n FROM pages WHERE status IN ('pending_vision','failed')")['n'],
      'chunks': one('SELECT COUNT(*) n FROM chunks')['n'],
      'tables': one("SELECT COUNT(*) n FROM chunks WHERE kind='table'")['n'],
      'figures': one("SELECT COUNT(*) n FROM blocks WHERE kind='figure'")['n'],
      'reviewed_pages': one('SELECT COUNT(*) n FROM pages WHERE reviewed=1')['n'],
      'draft_documents': one('SELECT COUNT(*) n FROM documents WHERE draft=1')['n'],
    }
