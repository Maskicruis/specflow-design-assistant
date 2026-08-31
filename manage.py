"""Local maintenance: stop, portable data backup, source check, safe Git update."""
import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from app.config import ROOT, DATA, SOURCES, INSTANCE
from launcher import health


def stop():
    info = health()
    if not info:
        print('服务未运行。')
        return
    if info.get('app') != 'SpecFlow' or info.get('instance') != INSTANCE:
        raise RuntimeError('8765 端口不属于此目录，拒绝停止其他服务。')
    db_path = DATA / 'knowledge.sqlite3'
    if db_path.exists():
        with closing(sqlite3.connect(db_path)) as db:
            busy = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchone()[0]
        if busy:
            raise RuntimeError('仍有解析任务。请在网页停止视觉任务、等待导入完成，再执行维护。')
    os.kill(int(info['pid']), signal.SIGTERM)
    for _ in range(40):
        current = health()
        if not current or current.get('pid') != info['pid']:
            (DATA / 'server.pid').unlink(missing_ok=True)
            print('本目录的服务已停止。')
            return
        time.sleep(.25)
    raise RuntimeError('服务尚未退出，请稍后重试。')


def ensure_stopped():
    if health():
        raise RuntimeError('请先停止本地服务：python manage.py stop。')


def backup():
    ensure_stopped()
    target_dir = ROOT / 'work' / 'backups'
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = target_dir / f'specflow-data-{stamp}.zip'
    db_path = DATA / 'knowledge.sqlite3'
    with tempfile.TemporaryDirectory(prefix='specflow-backup-') as scratch:
        snapshot = Path(scratch) / 'knowledge.sqlite3'
        if db_path.exists():
            with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
        try:
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                if snapshot.exists():
                    archive.write(snapshot, 'data/knowledge.sqlite3')
                for root, prefix in ((DATA, 'data'), (SOURCES, '规范文件')):
                    if not root.exists():
                        continue
                    for path in sorted(root.rglob('*')):
                        if not path.is_file() or path.is_symlink():
                            continue
                        if root == DATA and path.parent == DATA and path.name in {
                            'knowledge.sqlite3', 'knowledge.sqlite3-wal', 'knowledge.sqlite3-shm', 'server.pid'
                        }:
                            continue
                        archive.write(path, prefix + '/' + path.relative_to(root).as_posix())
                archive.writestr('BACKUP_INFO.txt',
                    'SpecFlow private data backup. Contains standards and derived content.\n'
                    'Do NOT upload to GitHub. Extract data and 规范文件 into a stopped installation.\n'
                    'No API keys included. Re-create .env on the destination computer.\n')
        except Exception:
            target.unlink(missing_ok=True)
            raise
    print(f'备份完成（含私人资料，请勿上传 GitHub）：{target}')
    return target


def run_git(*args, capture=False):
    result = subprocess.run(['git', *args], cwd=ROOT, check=True, text=True,
                            encoding='utf-8', capture_output=capture)
    return result.stdout.strip() if capture else None


def update():
    if Path(run_git('rev-parse', '--show-toplevel', capture=True)).resolve() != ROOT:
        raise RuntimeError('此目录不是独立 Git 仓库，请使用 git clone 安装。')
    if run_git('status', '--porcelain', capture=True):
        raise RuntimeError('程序源码有本地修改或未跟踪文件，请先自行保存；更新不会覆盖它们。')
    stop()
    if DATA.exists():
        backup()
    run_git('pull', '--ff-only')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')],
                   cwd=ROOT, check=True)
    print('程序已更新；私人资料和 .env 保留。请重新启动应用。')


def check():
    from app import store
    store.init()
    missing = []
    for doc in store.rows('SELECT id,name,path FROM documents'):
        try:
            exists = store.resolve_source(doc['path']).is_file()
        except ValueError:
            exists = False
        if not exists:
            missing.append({'id': doc['id'], 'name': doc['name']})
    print(json.dumps({'documents': store.stats()['documents'], 'missing_originals': missing}, ensure_ascii=False, indent=2))
    if missing:
        raise RuntimeError('部分原文件缺失。请迁移规范文件和 data/imports，或重新导入同一文件。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('stop', 'backup', 'update', 'check'))
    args = parser.parse_args()
    try:
        globals()[args.command]()
    except Exception as exc:
        print(f'维护失败：{exc}', file=sys.stderr)
        sys.exit(1)
