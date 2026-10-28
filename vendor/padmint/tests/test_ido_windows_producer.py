"""Include the offline compiler-producer checks in the regular test suite."""
import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / "scripts/ido-windows/test_build.py"
    spec = importlib.util.spec_from_file_location("ido_windows_producer_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
