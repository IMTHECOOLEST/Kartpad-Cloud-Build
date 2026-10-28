#!/usr/bin/env python3
"""Prepare the pinned upstream Python asm-processor without requiring a shell."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_process(data):
    replacements = (
        (b'import tempfile\n', b'import tempfile\nimport shlex\nimport subprocess\n'),
        (b"tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.o', delete=False)",
         b"tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.o', delete=False, dir='.' if os.name == 'nt' else None)"),
        (b"tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.s', delete=False)",
         b"tempfile.NamedTemporaryFile(prefix='asm-processor', suffix='.s', delete=False, dir='.' if os.name == 'nt' else None)"),
        (b'ret = os.system(assembler + " " + s_name + " -o " + o_name)',
         b'ret = subprocess.call(shlex.split(assembler) + [os.path.basename(s_name) if os.name == "nt" else s_name, "-o", os.path.basename(o_name) if os.name == "nt" else o_name])'),
    )
    for before, after in replacements:
        if data.count(before) != 1:
            raise ValueError('Pinned process-call patch no longer matches')
        data = data.replace(before, after)
    return data


def prepare(source, output):
    """Return prepared build.py; source is read-only and output must be new."""
    source, output = Path(source), Path(output)
    lock = json.loads((HERE / 'inputs.json').read_text(encoding='utf-8'))
    files = {}
    for name, pin in lock['files'].items():
        data = (source / name).read_bytes()
        if len(data) != pin['size'] or digest(data) != pin['sha256']:
            raise ValueError('Pinned source mismatch: ' + name)
        files[name] = data
    files['asm_processor.py'] = patch_process(files['asm_processor.py'])
    if digest(files['asm_processor.py']) != lock['patched_sha256']:
        raise ValueError('Patched source checksum mismatch')
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (output / name).write_bytes(data)
    receipt = {
        'source_commit': lock['commit'],
        'files': {name: digest(data) for name, data in files.items()},
        'change': 'Invoke the assembler directly; use relative temporary filenames on Windows.',
    }
    (output / 'provenance.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return output / 'build.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.source, args.output))


if __name__ == '__main__':
    main()
