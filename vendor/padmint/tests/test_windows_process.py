"""Real Windows console and cancellation checks with bounded public children."""
import ctypes
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from padmint import cli


@unittest.skipUnless(os.name == 'nt', 'requires Windows process and console APIs')
class WindowsProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='PadMint process 雪 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_private_console_keeps_arguments_environment_exit_and_parent_code_page(self):
        kernel = ctypes.windll.kernel32
        before = (kernel.GetConsoleCP(), kernel.GetConsoleOutputCP())
        script = '''import ctypes,json,os,sys
k=ctypes.windll.kernel32
result=[k.GetConsoleCP(),k.GetConsoleOutputCP(),sys.argv[1:],os.environ['PADMINT_LITERAL']]
os.write(1,json.dumps(result,ensure_ascii=False).encode('utf-8'))
sys.exit(17)
'''
        literal = 'literal & %PATH% ! 雪 "quoted"'
        log = self.root / 'output.log'
        result = cli.run_process([sys.executable, '-c', script, literal], self.root,
                                 log, self.root / 'events', lambda *a, **k: None,
                                 env=dict(os.environ, PADMINT_LITERAL=literal))
        self.assertEqual(result, (17, False))
        self.assertEqual(json.loads(log.read_text(encoding='utf-8')),
                         [65001, 65001, [literal], literal])
        self.assertEqual((kernel.GetConsoleCP(), kernel.GetConsoleOutputCP()), before)

    def test_window_cancel_stops_backend_and_grandchild_and_records_cancellation(self):
        # The window sends CTRL_BREAK_EVENT to the CLI's process group. The CLI
        # must catch it and stop the separately grouped backend's entire tree.
        backend = '''import json,os,pathlib,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(30)
'''
        runner = '''import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from padmint.cli import run_process
root=pathlib.Path(sys.argv[2])
result=run_process([sys.executable,'-c',sys.argv[3],str(root/'ready.json')],root,
                   root/'backend.log',root/'events',lambda *a,**k:None)
(root/'result.json').write_text(json.dumps(result))
'''
        previous = self.root / 'previous.ipa'
        previous.write_bytes(b'public fixture: keep previous output')
        package = str(Path(cli.__file__).resolve().parents[1])
        with (self.root / 'runner.log').open('wb') as log:
            child = subprocess.Popen([sys.executable, '-B', '-c', runner, package,
                                      str(self.root), backend], stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            owned = []
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and child.poll() is None:
                    if (self.root / 'ready.json').exists():
                        try:
                            owned = json.loads((self.root / 'ready.json').read_text())
                            break
                        except ValueError:
                            pass
                    time.sleep(.05)
                self.assertEqual(len(owned), 2, 'owned backend did not start')
                child.send_signal(signal.CTRL_BREAK_EVENT)
                child.wait(timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertEqual(json.loads((self.root / 'result.json').read_text()), [130, True])
                for pid in owned:
                    self.assertFalse(self.process_alive(pid), 'cancel left an owned child running')
                self.assertEqual(previous.read_bytes(), b'public fixture: keep previous output')
            finally:
                # Only these freshly spawned test processes may be stopped.
                for pid in owned:
                    if self.process_alive(pid):
                        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
                if child.poll() is None:
                    subprocess.run(['taskkill', '/T', '/F', '/PID', str(child.pid)], capture_output=True)
                    child.wait(timeout=5)

    @unittest.skipUnless(shutil.which('git'), 'Git is not installed for this packaged test')
    def test_git_checkout_root_preserves_a_non_english_path(self):
        root = self.root / 'PadMint checkout 雪'
        root.mkdir()
        subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
        self.assertEqual(Path(cli.git(root, 'rev-parse', '--show-toplevel')).resolve(), root.resolve())

    @staticmethod
    def process_alive(pid):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # An exited process no longer has a PID to open.
                return False
            raise ctypes.WinError(error)
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x102):
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 0x102
        finally:
            kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
