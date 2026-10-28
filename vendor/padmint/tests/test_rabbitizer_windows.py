"""Include offline Rabbitizer producer checks in regular test discovery."""
import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / 'scripts/rabbitizer-windows/test_build.py'
    spec = importlib.util.spec_from_file_location('rabbitizer_windows_tests', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
