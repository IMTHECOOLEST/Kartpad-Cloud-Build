#!/usr/bin/env python3
"""Compile a hand-authored GLOBAL_ASM fixture with existing pinned tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


def check_object(data):
    if data[:6] != b'\x7fELF\x01\x02':
        raise ValueError('Expected a 32-bit big-endian ELF object')
    header = struct.unpack_from('>HHIIIIIHHHHHH', data, 16)
    if header[0] != 1 or header[1] != 8:
        raise ValueError('Expected a MIPS relocatable object')
    headers = [struct.unpack_from('>IIIIIIIIII', data, header[5] + i * header[10])
               for i in range(header[11])]
    table = headers[header[12]]
    names = data[table[4]:table[4] + table[5]]
    sections = {names[s[0]:].split(b'\0')[0].decode(): data[s[4]:s[4] + s[5]] for s in headers}
    expected = {'.text': '2402000703e0000800000000', '.data': '0000000d'}
    for name, prefix in expected.items():
        if not sections.get(name, b'').startswith(bytes.fromhex(prefix)):
            raise ValueError('Wrong public fixture section: ' + name)
    return {name: sections[name].hex() for name in expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', required=True, type=Path)
    parser.add_argument('--compiler', required=True, type=Path)
    parser.add_argument('--assembler', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    source = (Path(__file__).resolve().parent / 'probe.c').read_bytes()
    (work / 'fixture.c').write_bytes(source)
    # The test driver also works with Windows embeddable Python's isolated path.
    # Game adapters use their existing Python-script entry point for this.
    runner = ('import runpy,sys; from pathlib import Path; p=Path(sys.argv[1]); '
              'sys.path.insert(0,str(p.parent)); sys.argv=sys.argv[1:]; '
              'runpy.run_path(str(p),run_name="__main__")')
    command = [sys.executable, '-B', '-c', runner, str(args.tool.resolve()),
               '--input-enc=utf-8', '--output-enc=EUC-JP', str(args.compiler.resolve()),
               '--', str(args.assembler.resolve()), '-32', '-G0', '-EB', '-march=vr4300',
               '--', '-G', '0', '-non_shared', '-mips2', '-O2', '-c', '-o', 'output.o', 'fixture.c']
    env = dict(os.environ, TMPDIR='/tmp', PYTHONUTF8='1')
    result = subprocess.run(command, cwd=work, env=env, capture_output=True)
    (work / 'probe.log').write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    data = (work / 'output.o').read_bytes()
    receipt = {'object_sha256': hashlib.sha256(data).hexdigest(),
               'source_sha256': hashlib.sha256(source).hexdigest(),
               'sections': check_object(data), 'command': command}
    (work / 'result.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print('PASS: public GLOBAL_ASM instructions and C data match')


if __name__ == '__main__':
    main()
