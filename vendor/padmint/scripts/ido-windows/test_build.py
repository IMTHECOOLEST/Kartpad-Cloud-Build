import hashlib
import importlib.util
import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('ido_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder, probe = load('build'), load('probe')


class InputTests(unittest.TestCase):
    def test_bad_cached_hash_is_preserved_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            file = cache / 'input.bin'; file.write_bytes(b'changed')
            lock = {'inputs': {'input': {'file': file.name, 'sha256': '0' * 64,
                                         'size': 7, 'url': 'https://example.invalid/input'}}}
            with patch.object(builder.urllib.request, 'urlopen') as network:
                with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                    builder.inputs(lock, cache, True)
                network.assert_not_called()
            self.assertEqual(file.read_bytes(), b'changed')

    def test_offline_missing_input_fails_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = {'inputs': {'input': {'file': 'input.bin', 'sha256': '0' * 64,
                                         'size': 1, 'url': 'https://example.invalid/input'}}}
            with patch.object(builder.urllib.request, 'urlopen') as network:
                with self.assertRaisesRegex(ValueError, 'Missing pinned input'):
                    builder.inputs(lock, Path(folder), False)
                network.assert_not_called()

    def test_failed_fetch_cleans_only_its_tempfile_and_retries(self):
        data = b'verified input'
        pin = {'file': 'input.bin', 'sha256': hashlib.sha256(data).hexdigest(),
               'size': len(data), 'url': 'https://example.invalid/input'}
        class Interrupted(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('interrupted after writing partial bytes')
                return super().read(size)

        for failure in (Interrupted(b'partial bytes'), io.BytesIO(b'wrong bytes')):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                cache = Path(folder)
                preserved = cache / 'unrelated.partial'; preserved.write_bytes(b'leave me alone')
                with patch.object(builder.urllib.request, 'urlopen', side_effect=[failure, io.BytesIO(data)]):
                    with self.assertRaises((OSError, ValueError)):
                        builder.inputs({'inputs': {'input': pin}}, cache, True)
                    self.assertEqual(list(cache.iterdir()), [preserved])
                    result = builder.inputs({'inputs': {'input': pin}}, cache, True)
                self.assertEqual(result['input'].read_bytes(), data)
                self.assertEqual(preserved.read_bytes(), b'leave me alone')
                self.assertEqual(sorted(file.name for file in cache.iterdir()), ['input.bin', 'unrelated.partial'])

    def test_correct_cache_still_requires_pinned_size(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'input.bin'; file.write_bytes(b'value')
            pin = {'file': file.name, 'sha256': builder.sha256(file), 'size': 6}
            with self.assertRaisesRegex(ValueError, 'Wrong input size'):
                builder.inputs({'inputs': {'input': pin}}, Path(folder), False)

    def test_unsafe_archive_names_are_rejected(self):
        for name in ('../escape', '/absolute', 'C:/absolute', 'folder\\escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.safe_name(name)

    def test_selected_source_avoids_unused_case_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root / 'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                for name, data in [('pin/code.c', b'code'), ('pin/ido/libC.so.1', b'C'),
                                   ('pin/ido/libc.so.1', b'c')]:
                    member = tarfile.TarInfo(name); member.size = len(data)
                    bundle.addfile(member, io.BytesIO(data))
            builder.extract_gzip(archive, root / 'out', 'pin', lambda name: not name.startswith('ido/'))
            self.assertEqual((root / 'out/code.c').read_bytes(), b'code')
            self.assertFalse((root / 'out/ido').exists())

    def test_link_cannot_escape_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root / 'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                member = tarfile.TarInfo('link'); member.type = tarfile.SYMTYPE
                member.linkname = '../../private'; bundle.addfile(member)
            with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
                builder.extract_gzip(archive, root / 'out')

    def test_changed_runtime_refuses_patch_before_writing(self):
        lock = json.loads((HERE / 'inputs.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder); file = source / 'libc_impl.c'; file.write_bytes(b'different runtime')
            run = Mock()
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                builder.apply_patch(source, lock, 'git', run)
            run.assert_not_called()
            self.assertEqual(file.read_bytes(), b'different runtime')


class CatalogTests(unittest.TestCase):
    def test_source_catalog_replaces_only_release_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source'; destination = root / 'catalog'
            source.write_bytes(b'first\nsecond\n'); destination.write_bytes(b'first\r\nsecond\r\n')
            compiler = root / 'cc.exe'; compiler.write_bytes(b'unchanged compiler')
            builder.install_catalog53(source, destination, builder.sha256(source))
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(compiler.read_bytes(), b'unchanged compiler')

    def test_changed_source_hash_preserves_release_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source'; destination = root / 'catalog'
            source.write_bytes(b'first\n'); destination.write_bytes(b'first\r\n')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                builder.install_catalog53(source, destination, '0' * 64)
            self.assertEqual(destination.read_bytes(), b'first\r\n')

    def test_different_release_message_preserves_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source'; destination = root / 'catalog'
            source.write_bytes(b'first\n'); destination.write_bytes(b'different\r\n')
            with self.assertRaisesRegex(ValueError, 'differs from the pinned source'):
                builder.install_catalog53(source, destination, builder.sha256(source))
            self.assertEqual(destination.read_bytes(), b'different\r\n')


class OutputTests(unittest.TestCase):
    def test_zero_exit_without_expected_output_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'did not write the expected output'):
                probe.verify_object(Path(folder) / 'result output.o', '0' * 64)

    def test_wrong_architecture_is_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'wrong.o'; file.write_bytes(b'\x7fELF\x02\x01' + bytes(64))
            with self.assertRaisesRegex(ValueError, 'Not a big-endian MIPS object'):
                probe.verify_object(file, builder.sha256(file))

    def test_different_generated_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'mips.o'; data = bytearray(52)
            data[:6] = b'\x7fELF\x01\x02'; struct.pack_into('>H', data, 18, 8); file.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'differs from the pinned Mac reference'):
                probe.verify_object(file, '0' * 64)
            self.assertEqual(probe.verify_object(file, hashlib.sha256(data).hexdigest())['bytes'], 52)


if __name__ == '__main__':
    unittest.main()
