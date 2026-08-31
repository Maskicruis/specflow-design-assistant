"""Isolate storage before application modules initialize during collection."""
import os
import tempfile
from pathlib import Path


def pytest_configure(config):
    config.specflow_old_env = {key: os.environ.get(key) for key in ('SPEC_DATA_DIR', 'SPEC_SOURCES_DIR')}
    config.specflow_temp = tempfile.TemporaryDirectory(prefix='specflow-test-session-')
    root = Path(config.specflow_temp.name)
    os.environ['SPEC_DATA_DIR'] = str(root / 'data')
    os.environ['SPEC_SOURCES_DIR'] = str(root / 'sources')


def pytest_unconfigure(config):
    for key, value in config.specflow_old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config.specflow_temp.cleanup()
