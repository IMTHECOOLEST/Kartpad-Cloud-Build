"""Include offline asm-processor preparation checks in regular test discovery."""
import importlib.util
from pathlib import Path


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / "scripts/asm-processor-windows/test_prepare.py"
    spec = importlib.util.spec_from_file_location("asm_processor_windows_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
