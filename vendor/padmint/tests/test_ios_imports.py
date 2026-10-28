"""Portable import classification regression tests; no compiler or shell fixtures."""
import io
from pathlib import Path
import unittest
from unittest import mock

from padmint import ios_module


class ImportCheckTests(unittest.TestCase):
    """Platform-independent symbol classification, with no stand-in shell executables."""

    def check(self, imports, app_exports=(), demangled=""):
        output = io.StringIO()
        with mock.patch.object(ios_module, "llvm_tool", side_effect=lambda root, name: root / name), \
                mock.patch.object(ios_module, "_names", side_effect=[set(imports), set(app_exports)]), \
                mock.patch.object(ios_module.subprocess, "run", return_value=mock.Mock(stdout=demangled)):
            ios_module.check_imports(Path("llvm"), Path("module"), Path("app"), output)
        return output.getvalue()

    def test_missing_c_callbacks_are_not_assumed_to_be_device_libraries(self):
        # These are the C-linkage imports the first empty SquirrelPad shell omitted.
        for name in ("_conker_interpreter_exit", "_conker_kseg1_read32", "_osPiRawReadIo_recomp",
                     "_conker_widen_sprite_cull_left", "_recomp_syscall_handler", "_app_function",
                     "_bogus_runtime_symbol", "_memcpy_typo", "_getenv_typo", "_atexit_game", "_strtol_typo",
                     "_pthread_missing", "_pthread_create_recomp", "___cxa_not_a_runtime_function",
                     "__tlv_bootstrap_typo", "__tlv_bootstrap_recomp", "_tlv_bootstrap"):
            with self.subTest(name=name), self.assertRaisesRegex(ios_module.ModuleError, name) as failure:
                self.check([name])
            self.assertIn("legitimate system-library names need review", str(failure.exception))
            self.assertIn("availability on iOS is unverified", str(failure.exception))

    def test_c_callbacks_are_allowed_when_the_app_really_exports_them(self):
        names = ["_conker_interpreter_exit", "_osPiRawReadIo_recomp", "_recomp_syscall_handler"]
        self.assertIn("3 from the app, 0 known runtime names", self.check(names, names))

    def test_ordinary_c_memory_strings_and_file_io_do_not_need_an_app_export(self):
        names = ["_malloc", "_calloc", "_realloc", "_free", "_memcmp", "_strcmp", "_strcpy",
                 "_strncpy", "_strerror", "_strchr", "_fopen", "_fclose", "_fread", "_fwrite",
                 "_fseek", "_ftell", "_puts", "_vsnprintf", "___memcpy_chk", "___stdoutp"]
        self.assertIn(f"0 from the app, {len(names)} known runtime names", self.check(names))

    def test_recorded_probe_and_game_runtime_imports_remain_allowed(self):
        # Union of actual C/runtime imports in the LLVM21/22 SDK probes, KartPad
        # v0.7.9, SunPad v0.2.2 Linux modules, and the SquirrelPad candidate module.
        imports = """__Unwind_Resume ___cxa_allocate_exception ___cxa_atexit ___cxa_begin_catch
            ___cxa_end_catch ___cxa_free_exception ___cxa_guard_abort ___cxa_guard_acquire
            ___cxa_guard_release ___cxa_throw ___gxx_personality_v0 ___stack_chk_fail
            ___stack_chk_guard ___assert_rtn ___stderrp _abort _bzero _feclearexcept
            _fetestexcept _fegetround _fesetround _fflush _fprintf _printf _ldexp _ldexpf
            _longjmp _memcpy _memmove _memset _mmap _mprotect _munmap _setjmp _snprintf
            _strlen _getenv dyld_stub_binder""".split()
        self.assertIn(f"{len(imports)} known runtime names, 0 standard C++ names", self.check(imports))

    def test_cpp_namespace_classification_stays_explicitly_limited(self):
        self.assertIn("classified by namespace; device availability not verified", self.check(["__ZNSt3__14coutE"], demangled="std::cout\n"))
        with self.assertRaisesRegex(ios_module.ModuleError, "does not export.*Game::missing"):
            self.check(["__ZN4Game7missingEv"], demangled="Game::missing()\n")
        with self.assertRaisesRegex(ios_module.ModuleError, "could not classify every"):
            self.check(["__ZN4Game7missingEv"], demangled="")

    def test_recorded_bluewake_worker_pool_imports_are_exact_runtime_names(self):
        names = ["_atexit", "_strtol", "_pthread_create", "_pthread_join", "_pthread_once",
                 "_pthread_mutex_lock", "_pthread_mutex_unlock", "_pthread_cond_broadcast",
                 "_pthread_cond_signal", "_pthread_cond_wait"]
        self.assertIn("0 from the app, 10 known runtime names", self.check(names))

    def test_native_thread_local_bootstrap_is_an_exact_runtime_name(self):
        self.assertIn("0 from the app, 1 known runtime names", self.check(["__tlv_bootstrap"]))


if __name__ == "__main__":
    unittest.main()
