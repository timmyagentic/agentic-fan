import ctypes as C
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agenticfan.codecs import KeyInfo, key_number
from agenticfan.errors import AgenticFanError, MissingKey
from agenticfan.runtime import lock, runtime_directory
from agenticfan.smc import AppleSMC, SMCData


class TransportTests(unittest.TestCase):
    """Exercise the native ABI boundary without pretending to have Apple hardware."""

    def setUp(self):
        self.smc = AppleSMC.__new__(AppleSMC)
        self.smc.connection = C.c_uint32(1)
        self.smc.cache = {}
        self.commands = []

    def install_call(self, callback):
        def call(connection, selector, request, size, response, response_size):
            self.assertEqual(selector, 2)
            self.assertEqual(size, 80)
            req = C.cast(request, C.POINTER(SMCData)).contents
            out = C.cast(response, C.POINTER(SMCData)).contents
            length = C.cast(response_size, C.POINTER(C.c_size_t)).contents
            self.commands.append(req.command)
            return callback(req, out, length)
        self.smc.call_method = call

    def test_per_key_metadata_and_payload_travel_through_abi(self):
        def call(req, out, length):
            self.assertEqual(req.key, key_number("F0Tg"))
            if req.command == 9:
                out.info.size = 2
                out.info.kind = key_number("fpe2")
            else:
                self.assertEqual(req.info.size, 2)
                if req.command == 5:
                    out.payload[:2] = bytes.fromhex("2ee0")
                if req.command == 6:
                    self.assertEqual(bytes(req.payload[:2]), bytes.fromhex("3200"))
            return 0

        self.install_call(call)
        self.assertEqual(self.smc.read("F0Tg"), bytes.fromhex("2ee0"))
        self.smc.write("F0Tg", bytes.fromhex("3200"))
        self.assertEqual(self.commands, [9, 5, 6])

    def test_successful_kernel_call_does_not_hide_smc_error(self):
        def call(req, out, length):
            out.result = 1
            return 0
        self.install_call(call)
        with self.assertRaises(AgenticFanError) as caught:
            self.smc.info("F0Tg")
        self.assertEqual(caught.exception.code, "SMC_COMMAND_FAILED")

    def test_only_explicit_not_found_is_absence(self):
        def call(req, out, length):
            out.result = 0x84
            return 0
        self.install_call(call)
        with self.assertRaises(MissingKey):
            self.smc.info("F0md")

    def test_kernel_error_is_reported(self):
        self.install_call(lambda req, out, length: 0xE00002C1)
        with self.assertRaises(AgenticFanError) as caught:
            self.smc.info("F0Tg")
        self.assertEqual(caught.exception.code, "SMC_IO_ERROR")

    def test_short_response_rejected(self):
        def call(req, out, length):
            length.value = 79
            return 0
        self.install_call(call)
        with self.assertRaises(AgenticFanError) as caught:
            self.smc.info("F0Tg")
        self.assertEqual(caught.exception.code, "SMC_SHORT_RESPONSE")

    def test_oversize_info_rejected_before_read(self):
        def call(req, out, length):
            out.info.size = 33
            return 0
        self.install_call(call)
        with self.assertRaises(AgenticFanError) as caught:
            self.smc.read("Tbad")
        self.assertEqual(caught.exception.code, "SMC_INVALID_SIZE")
        self.assertEqual(self.commands, [9])


class RuntimeTests(unittest.TestCase):
    def test_shared_or_symlink_demo_directory_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent) / "state"
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)  # Do not let the runner's umask make this fixture private.
            with patch.dict(os.environ, {"AGENTICFAN_DEMO_DIR": str(directory)}):
                with self.assertRaises(AgenticFanError):
                    runtime_directory(True)
            directory.chmod(0o700)
            link = Path(parent) / "link"
            link.symlink_to(directory)
            with patch.dict(os.environ, {"AGENTICFAN_DEMO_DIR": str(link)}):
                with self.assertRaises(AgenticFanError):
                    runtime_directory(True)

    def test_lock_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            target = directory / "target"
            target.write_text("must not be modified")
            (directory / "session.lock").symlink_to(target)
            with self.assertRaises(OSError):
                with lock(directory, "session.lock"):
                    self.fail("followed a symlink")
            self.assertEqual(target.read_text(), "must not be modified")


if __name__ == "__main__":
    unittest.main()
