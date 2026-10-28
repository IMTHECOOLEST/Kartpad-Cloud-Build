#!/usr/bin/env python3
"""Verify isolated IDO runtimes against hand-authored C and exact reference objects."""
import argparse
import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify_object(path, expected):
    if not path.is_file():
        raise ValueError(f'Compiler did not write the expected output: {path}')
    raw = path.read_bytes()
    if raw[:6] != b'\x7fELF\x01\x02' or len(raw) < 52 or struct.unpack_from('>H', raw, 18)[0] != 8:
        raise ValueError(f'Not a big-endian MIPS object: {path}')
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'Compiler output differs from the pinned Mac reference: {path}')
    return {'sha256': expected, 'bytes': len(raw)}


def check_diagnostics(compiler, cwd, env):
    """Exercise the byte-indexed message catalog as well as compiler exit status."""
    cwd.mkdir()
    cases = (('warning', b'int example(int x) {\r\n return x + 1;\r\n}\r\n'),
             ('invalid', b'int example(int x) { return ?; }\n'))
    for name, source in cases:
        (cwd / (name + '.c')).write_bytes(source)
        command = [str(compiler.resolve()), '-G', '0', '-non_shared', '-O2', '-mips2',
                   '-c', '-o', name + '.o', name + '.c']
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True)
        output = result.stdout + result.stderr
        (cwd / (name + '.log')).write_bytes(output)
        object_exists = (cwd / (name + '.o')).is_file()
        if name == 'warning':
            valid = (result.returncode == 0 and object_exists and
                     b'Unknown control character' in output and b'Too many errors' not in output)
        else:
            valid = (result.returncode != 0 and not object_exists and b'Syntax Error' in output)
        if not valid:
            raise ValueError(f'IDO diagnostic catalog or exit status is incorrect; see {cwd / (name + ".log")}')


def probe(root, work):
    expected = json.loads((HERE / 'probe-expected.json').read_text())['objects']
    work.mkdir(parents=True, exist_ok=False)
    rows = []
    for version, cases in expected.items():
        tool_root = root / ('ido' + version.replace('.', ''))
        compiler = tool_root / 'usr/bin/cc.exe'
        if not (tool_root / 'tmp').is_dir():
            raise ValueError(f'Missing isolated MSYS tmp folder: {tool_root}')
        for optimization, digest in cases.items():
            cwd = work / (version + ' ' + optimization + ' path with spaces')
            cwd.mkdir()
            (cwd / 'fixture.c').write_bytes((HERE / 'probe.c').read_bytes())
            env = os.environ.copy()
            env['TMPDIR'] = '/tmp'
            env['USR_LIB'] = '/usr/bin'
            command = [str(compiler.resolve()), '-G', '0', '-non_shared', '-' + optimization,
                       '-mips2', '-c', '-o', 'output.o', 'fixture.c']
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True)
            (cwd / 'compiler.log').write_bytes(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError(f'IDO {version} {optimization} failed; see {cwd / "compiler.log"}')
            row = {'ido': version, 'optimization': optimization,
                   **verify_object(cwd / 'output.o', digest)}
            rows.append(row)
        check_diagnostics(compiler, work / (version + ' diagnostics'), env)
    (work / 'result.json').write_text(json.dumps({'cases': rows}, indent=2) + '\n')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Contains isolated ido53/ and ido71/ runtimes')
    parser.add_argument('--work', type=Path, required=True, help='New synthetic probe folder')
    args = parser.parse_args()
    try:
        rows = probe(args.root.resolve(), args.work.resolve())
    except (OSError, ValueError) as error:
        parser.exit(1, f'IDO probe failed: {error}\n')
    print(f'PASS: {len(rows)} IDO compiler objects match the pinned Mac reference byte for byte; diagnostics passed')


if __name__ == '__main__':
    main()
