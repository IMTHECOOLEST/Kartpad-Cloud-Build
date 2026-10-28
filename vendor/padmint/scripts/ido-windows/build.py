#!/usr/bin/env python3
"""Build the pinned IDO Windows tools locally; never install or publish them.

Run in a Visual Studio Native Tools environment. Managed LLVM, CMake, Ninja and
Git are explicit inputs. Downloads require --fetch; an existing work folder is
never reused. IDO5.3 stays the official binary, while IDO7.1 is built from source.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def checked(path, expected):
    if sha256(path) != expected:
        raise ValueError(f'Checksum mismatch: {path}')
    return Path(path)


def safe_name(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe archive path: {name}')
    return path


def inputs(lock, cache, fetch):
    cache.mkdir(parents=True, exist_ok=True)
    found = {}
    for key, pin in lock['inputs'].items():
        name = safe_name(pin['file'])
        if len(name.parts) != 1:
            raise ValueError('Input cache names must be basenames')
        path = cache / name.name
        if not path.exists():
            if not fetch:
                raise ValueError(f'Missing pinned input: {path}; add it or pass --fetch')
            descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.partial', dir=cache)
            partial = Path(temporary)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    with urllib.request.urlopen(pin['url'], timeout=60) as response:
                        shutil.copyfileobj(response, stream)
                checked(partial, pin['sha256'])
                if partial.stat().st_size != pin['size']:
                    raise ValueError(f'Wrong input size: {partial}')
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        found[key] = checked(path, pin['sha256'])
        if path.stat().st_size != pin['size']:
            raise ValueError(f'Wrong input size: {path}')
    return found


def extract_gzip(archive, target, prefix='', selected=None):
    """Only regular files are needed. Resolve archive-internal links by copying."""
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle.getmembers():
            path = safe_name(member.name)
            if prefix:
                if path.parts[0] != prefix:
                    raise ValueError('Unexpected source archive root')
                path = PurePosixPath(*path.parts[1:])
            if not path.parts or member.isdir() or (selected is not None and not selected(path.as_posix())):
                continue
            if member.issym() or member.islnk():
                safe_name(member.linkname)
            if not (member.isfile() or member.issym() or member.islnk()):
                raise ValueError(f'Unsupported archive member: {member.name}')
            data = bundle.extractfile(member)
            if data is None:
                raise ValueError(f'Missing archive data: {member.name}')
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with data, destination.open('xb') as output:
                shutil.copyfileobj(data, output)


def install_catalog53(source, destination, expected):
    """IDO uses byte offsets into this catalog; CRLF release bytes break diagnostics."""
    data = checked(source, expected).read_bytes()
    if destination.read_bytes().replace(b'\r\n', b'\n') != data:
        raise ValueError('IDO5.3 release catalog differs from the pinned source')
    destination.write_bytes(data)


def apply_patch(source, lock, git, run):
    patch = checked(HERE / 'windows-runtime.patch', lock['patch_sha256'])
    for name, pin in lock['patched_files'].items():
        checked(source / name, pin['before'])
    run([git, '-c', 'core.autocrlf=false', '-C', source, 'apply', '--check', patch])
    run([git, '-c', 'core.autocrlf=false', '-C', source, 'apply', patch])
    for name, pin in lock['patched_files'].items():
        checked(source / name, pin['after'])


def build(args):
    if os.name != 'nt':
        raise ValueError('This producer runs on Windows in a Visual Studio Native Tools environment')
    if not shutil.which('cl'):
        raise ValueError('Run from a Visual Studio Native Tools prompt with the C++ build tools')
    work = args.work.resolve()
    if str(work).startswith('\\\\'):
        raise ValueError('Use a local Windows work folder; cmd.exe cannot link from a UNC working directory')
    lock = json.loads((HERE / 'inputs.json').read_text())
    clang, lld = args.llvm / 'clang.exe', args.llvm / 'ld.lld.exe'
    compiler_version = subprocess.check_output([str(clang), '--version'], text=True).splitlines()[0]
    if f'clang version {lock["llvm_version"]}' not in compiler_version:
        raise ValueError(f'Use the pinned LLVM {lock["llvm_version"]}; got {compiler_version}')
    if work.exists():
        raise ValueError(f'Use a new work folder; preserving {work}')
    downloads = inputs(lock, args.cache.resolve(), args.fetch)
    work.mkdir(parents=True)
    commands = []
    log = (work / 'build.log').open('wb')

    def run(argv, capture=False):
        command = [str(value) for value in argv]
        commands.append(command)
        result = subprocess.run(command, cwd=work, stdout=subprocess.PIPE if capture else log,
                                stderr=log, check=True)
        return result.stdout if capture else None

    try:
        source = work / 'source'
        wanted = {'ido/7.1/' + value['input'] for value in lock['tools71'].values()}
        wanted.update(('ido/7.1/usr/lib/err.english.cc', 'ido/5.3/usr/lib/err.english.cc',
                       'ido/5.3/LICENSE.md'))
        extract_gzip(downloads['source'], source, lock['source_prefix'],
                     lambda name: not name.startswith('ido/') or name in wanted)
        apply_patch(source, lock, args.git, run)
        sysroot, runtime = work / 'sysroot', work / 'runtime'
        for key, destination in (('devel', sysroot), ('w32headers', sysroot),
                                 ('w32runtime', sysroot), ('runtime', runtime)):
            destination.mkdir(exist_ok=True)
            names = run([args.cmake, '-E', 'tar', 'tf', downloads[key]], capture=True).decode().splitlines()
            for name in names:
                safe_name(name)
            members = []
            if key == 'runtime':
                members = list(lock['inputs']['runtime']['members'])
                if not set(members).issubset(names):
                    raise ValueError('The runtime archive is missing its DLL or required license notices')
            command = [str(args.cmake), '-E', 'tar', 'xf', str(downloads[key]), *members]
            commands.append(command)
            subprocess.run(command, cwd=destination, stdout=log, stderr=log, check=True)
            if key == 'runtime':
                for name, digest in lock['inputs']['runtime']['members'].items():
                    checked(destination / name, digest)
        crt = work / 'compiler-rt'
        for key, name in (('chkstk', 'lib/builtins/x86_64/chkstk.S'),
                          ('assembly', 'lib/builtins/assembly.h'), ('compiler_license', 'LICENSE.TXT')):
            target = crt / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(downloads[key], target)
        host = work / 'host'
        run([args.cmake, '-S', HERE, '-B', host, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=MinSizeRel',
             '-DCMAKE_MAKE_PROGRAM=' + str(args.ninja), '-DIDO_SOURCE:PATH=' + source.as_posix()])
        run([args.cmake, '--build', host, '--parallel', str(args.jobs)])
        generated, objects = work / 'generated', work / 'objects'
        generated.mkdir(); objects.mkdir()
        for name, pin in lock['tools71'].items():
            data = run([host / 'ido-recomp.exe', source / 'ido/7.1' / pin['input']], capture=True)
            file = generated / (name + '.c')
            file.write_bytes(data.replace(b'\r\n', b'\n'))
            checked(file, pin['generated_sha256'])
        common = [clang, '--target=x86_64-pc-cygwin', '-std=c11', '-Os', '-fno-strict-aliasing',
                  '-fmacro-prefix-map=' + str(work) + '=.', '-isystem', sysroot / 'usr/include',
                  '-isystem', sysroot / 'usr/include/w32api', '-I', source]
        run([clang, '--target=x86_64-pc-cygwin', '-c', crt / 'lib/builtins/x86_64/chkstk.S',
             '-o', objects / 'chkstk.o'])
        for version in ('53', '71'):
            run([*common, '-DIDO' + version, '-c', source / 'libc_impl.c', '-o', objects / ('libc' + version + '.o')])
        run([*common, '-DIDO71', '-DPACKAGE_VERSION="' + lock['revision'] + '"',
             '-DDATETIME="reproducible source build"', '-c', source / 'version_info.c', '-o', objects / 'version.o'])
        output = work / 'output'
        bin71 = output / 'ido71/usr/bin'; bin71.mkdir(parents=True)
        for name in lock['tools71']:
            obj = objects / (name + '.o')
            run([*common, '-c', generated / (name + '.c'), '-o', obj])
            # Match the fixed image base and stack of the upstream MSYS build;
            # LLD's ASLR defaults break fork in the compiler's O3 pipeline.
            run([lld, '-m', 'i386pep', '--subsystem', 'console', '--entry', 'mainCRTStartup',
                 '--image-base', '0x100400000', '--disable-dynamicbase', '--disable-high-entropy-va',
                 '--stack', '2097152', '--no-insert-timestamp', '-o', bin71 / (name + '.exe'),
                 sysroot / 'usr/lib/crt0.o', obj,
                 objects / ('libc53.o' if name == 'edgcpfe' else 'libc71.o'),
                 objects / 'version.o', objects / 'chkstk.o', '-L' + str(sysroot / 'usr/lib'),
                 '-L' + str(sysroot / 'usr/lib/w32api'), '-lmsys-2.0', '-lkernel32'])
        shutil.copyfile(bin71 / 'cc.exe', bin71 / 'NCC.exe')
        shutil.copyfile(source / 'ido/7.1/usr/lib/err.english.cc', bin71 / 'err.english.cc')
        extract_gzip(downloads['ido53'], output / 'ido53/usr/bin')
        install_catalog53(source / 'ido/5.3/usr/lib/err.english.cc',
                          output / 'ido53/usr/bin/err.english.cc', lock['catalog53_sha256'])
        for version in ('53', '71'):
            root = output / ('ido' + version)
            (root / 'tmp').mkdir()
            shutil.copyfile(runtime / 'usr/bin/msys-2.0.dll', root / 'usr/bin/msys-2.0.dll')
        notices = output / 'notices'; notices.mkdir()
        shutil.copytree(runtime / 'usr/share/doc/Cygwin', notices / 'msys')
        shutil.copyfile(crt / 'LICENSE.TXT', notices / 'compiler-rt-LICENSE.TXT')
        shutil.copyfile(source / 'ido/5.3/LICENSE.md', notices / 'ido-5.3-LICENSE.md')
        shutil.copyfile(source / 'README.md', notices / 'ido-upstream-README.md')
        for name in ('inputs.json', 'windows-runtime.patch'):
            shutil.copyfile(HERE / name, notices / name)
        run([os.sys.executable, HERE / 'probe.py', output, '--work', work / 'probe with spaces'])
        files = {file.relative_to(output).as_posix(): sha256(file)
                 for file in sorted(output.rglob('*')) if file.is_file()}
        report = {'revision': lock['revision'], 'source_commit': lock['source_commit'],
                  'compiler': compiler_version, 'input_lock_sha256': sha256(HERE / 'inputs.json'),
                  'files': files, 'commands': commands}
        (work / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
        return output
    finally:
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True, help='New local Windows folder')
    parser.add_argument('--cache', type=Path, required=True, help='Pinned archives, reusable across builds')
    parser.add_argument('--llvm', type=Path, required=True, help='Managed LLVM 21.1.8 bin folder')
    for name in ('cmake', 'ninja', 'git'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--jobs', type=int, choices=range(1, 9), default=1)
    parser.add_argument('--fetch', action='store_true', help='Download missing pinned inputs into cache')
    args = parser.parse_args()
    try:
        print(build(args))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'IDO build stopped: {error}\nPrevious work and input archives were preserved.\n')


if __name__ == '__main__':
    main()
