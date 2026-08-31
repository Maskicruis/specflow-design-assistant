"""Portable runtime configuration. Existing process variables take precedence."""
import hashlib
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False, interpolate=False)

def directory(name, default):
    path = Path(os.environ.get(name) or default).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()

DATA = directory('SPEC_DATA_DIR', 'data')
SOURCES = directory('SPEC_SOURCES_DIR', '规范文件')
INSTANCE = hashlib.sha256(str(ROOT).encode('utf-8')).hexdigest()[:16]
