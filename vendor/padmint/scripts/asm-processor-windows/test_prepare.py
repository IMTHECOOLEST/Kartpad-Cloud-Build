import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare_asm_processor', HERE / 'prepare.py')
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'; self.source.mkdir()
        self.output = self.root / 'prepared'
        self.originals = {
            'build.py': b'# unchanged entry point\n',
            'asm_processor.py': b'''import os
import tempfile
def assemble(assembler):
    o_file = tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.o', delete=False)
    o_name = o_file.name
    o_file.close()
    s_file = tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.s', delete=False)
    s_name = s_file.name
    try:
        s_file.write(b'public assembly fixture')
        s_file.close()
        ret = os.system(assembler + " " + s_name + " -o " + o_name)
        if ret != 0:
            raise RuntimeError("failed to assemble")
    finally:
        s_file.close()
        os.remove(s_name)
        os.remove(o_name)
''',
            'prelude.inc': b'# unchanged assembly prelude\n',
            'LICENSE': b'unchanged upstream license\n',
        }
        self.lock = {'commit': 'synthetic-pinned-source', 'files': {},
                     'patched_sha256': producer.digest(producer.patch_process(self.originals['asm_processor.py']))}
        for name, data in self.originals.items():
            (self.source / name).write_bytes(data)
            self.lock['files'][name] = {'size': len(data), 'sha256': producer.digest(data)}
        self.save_lock()
        here = patch.object(producer, 'HERE', self.root)
        here.start(); self.addCleanup(here.stop)

    def save_lock(self):
        (self.root / 'inputs.json').write_text(json.dumps(self.lock), encoding='utf-8')

    def test_prepare_preserves_inputs_license_and_entry_point(self):
        result = producer.prepare(self.source, self.output)
        self.assertEqual(result, self.output / 'build.py')
        for name, original in self.originals.items():
            self.assertEqual((self.source / name).read_bytes(), original)
            if name != 'asm_processor.py':
                self.assertEqual((self.output / name).read_bytes(), original)
        receipt = json.loads((self.output / 'provenance.json').read_text())
        self.assertEqual(receipt['source_commit'], self.lock['commit'])
        self.assertEqual(receipt['files']['asm_processor.py'], self.lock['patched_sha256'])

    def test_modified_source_fails_before_output_created(self):
        (self.source / 'LICENSE').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Pinned source mismatch: LICENSE'):
            producer.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_source_fails_before_output_created(self):
        (self.source / 'prelude.inc').unlink()
        with self.assertRaises(FileNotFoundError):
            producer.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_preexisting_output_is_preserved(self):
        self.output.mkdir(); preserved = self.output / 'keep'; preserved.write_bytes(b'old work')
        with self.assertRaises(FileExistsError):
            producer.prepare(self.source, self.output)
        self.assertEqual(list(self.output.iterdir()), [preserved])
        self.assertEqual(preserved.read_bytes(), b'old work')

    def test_changed_patch_output_fails_before_writing(self):
        self.lock['patched_sha256'] = '0' * 64; self.save_lock()
        with self.assertRaisesRegex(ValueError, 'Patched source checksum mismatch'):
            producer.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def check_invocation(self, windows, status):
        producer.prepare(self.source, self.output)
        namespace = {}; exec((self.output / 'asm_processor.py').read_bytes(), namespace)
        namespace['os'] = SimpleNamespace(name='nt' if windows else 'posix', path=os.path, remove=os.remove)
        arguments = [r'C:\Tools with spaces\as.exe', '-EB', "quote'and;$(no-shell)"]
        assembler = ' '.join(shlex.quote(arg) for arg in arguments)
        work = self.root / 'work with spaces \u96ea'; work.mkdir()
        ambient_temp = self.root / 'user temp \u96ea'; ambient_temp.mkdir()
        paths = []

        def invoke(command):
            self.assertEqual(command[:-3], arguments)
            self.assertEqual(command[-2], '-o')
            for name in (command[-3], command[-1]):
                path = Path(name)
                self.assertEqual(path.is_absolute(), not windows)
                if windows:
                    self.assertEqual(name, path.name)
                    self.assertTrue(name.isascii())
                resolved = path.resolve(); paths.append(resolved)
                self.assertEqual(resolved.parent, work if windows else ambient_temp)
                self.assertTrue(resolved.is_file())
            self.assertEqual(paths[0].read_bytes(), b'public assembly fixture')
            return status

        previous = Path.cwd()
        try:
            os.chdir(work)
            with patch.object(tempfile, 'tempdir', str(ambient_temp)), \
                    patch.object(namespace['subprocess'], 'call', side_effect=invoke) as call:
                if status:
                    with self.assertRaisesRegex(RuntimeError, 'failed to assemble'):
                        namespace['assemble'](assembler)
                else:
                    namespace['assemble'](assembler)
                call.assert_called_once()
        finally:
            os.chdir(previous)
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(list(ambient_temp.iterdir()), [])

    def test_windows_unicode_work_and_temp_keep_ascii_relative_arguments(self):
        self.check_invocation(windows=True, status=0)

    def test_windows_assembler_failure_still_cleans_temporary_files(self):
        self.check_invocation(windows=True, status=1)

    def test_other_hosts_keep_their_original_temporary_location(self):
        self.check_invocation(windows=False, status=0)


if __name__ == '__main__':
    unittest.main()
