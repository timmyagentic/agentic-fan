"""Small ctypes bridge to AppleSMC. Only the fan service exposes writes to the CLI."""
from __future__ import annotations

import ctypes as C
import sys

from .codecs import KeyInfo, key_number
from .errors import AgenticFanError, MissingKey


class Version(C.Structure):
    _fields_ = [("major", C.c_uint8), ("minor", C.c_uint8), ("build", C.c_uint8),
                ("reserved", C.c_uint8), ("release", C.c_uint16)]


class PowerLimit(C.Structure):
    _fields_ = [("version", C.c_uint16), ("length", C.c_uint16),
                ("cpu", C.c_uint32), ("gpu", C.c_uint32), ("memory", C.c_uint32)]


class KeyMetadata(C.Structure):
    _fields_ = [("size", C.c_uint32), ("kind", C.c_uint32), ("attributes", C.c_uint8)]


class SMCData(C.Structure):
    _fields_ = [("key", C.c_uint32), ("version", Version), ("power", PowerLimit),
                ("info", KeyMetadata), ("result", C.c_uint8), ("status", C.c_uint8),
                ("command", C.c_uint8), ("index", C.c_uint32), ("payload", C.c_uint8 * 32)]


def bind(lib, name, args, result):
    fn = getattr(lib, name)
    fn.argtypes = args
    fn.restype = result
    return fn


class AppleSMC:
    def __init__(self):
        if sys.platform != "darwin":
            raise AgenticFanError("UNSUPPORTED_PLATFORM", "Hardware access requires macOS. Use --demo for simulation.", 3)
        if C.sizeof(SMCData) != 80 or SMCData.payload.offset != 48:
            raise AgenticFanError("UNSUPPORTED_ABI", "Unexpected AppleSMC structure layout.", 3)
        self.io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        system = C.CDLL("/usr/lib/libSystem.B.dylib")
        match = bind(self.io, "IOServiceMatching", [C.c_char_p], C.c_void_p)
        service = bind(self.io, "IOServiceGetMatchingService", [C.c_uint32, C.c_void_p], C.c_uint32)
        open_service = bind(self.io, "IOServiceOpen", [C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)], C.c_uint32)
        self.release = bind(self.io, "IOObjectRelease", [C.c_uint32], C.c_uint32)
        self.close_service = bind(self.io, "IOServiceClose", [C.c_uint32], C.c_uint32)
        self.call_method = bind(self.io, "IOConnectCallStructMethod",
                                [C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p, C.POINTER(C.c_size_t)], C.c_uint32)
        entry = service(0, match(b"AppleSMC"))
        if not entry:
            raise AgenticFanError("SMC_UNAVAILABLE", "AppleSMC is unavailable on this machine.", 3)
        self.connection = C.c_uint32()
        try:
            task = C.c_uint32.in_dll(system, "mach_task_self_").value
            result = open_service(entry, task, 0, C.byref(self.connection))
        finally:
            self.release(entry)
        if result:
            raise AgenticFanError("SMC_OPEN_FAILED", "Cannot open AppleSMC.", kernel_result=hex(result))
        self.cache: dict[str, KeyInfo] = {}

    def _call(self, request: SMCData, key: str = "") -> SMCData:
        response = SMCData()
        size = C.c_size_t(C.sizeof(response))
        result = self.call_method(self.connection, 2, C.byref(request), C.sizeof(request), C.byref(response), C.byref(size))
        if result:
            raise AgenticFanError("SMC_IO_ERROR", "AppleSMC rejected the I/O request.",
                              key=key, command=request.command, kernel_result=hex(result))
        if size.value != C.sizeof(response):
            raise AgenticFanError("SMC_SHORT_RESPONSE", "AppleSMC returned an unexpected structure size.", size=size.value)
        if response.result == 0x84:
            raise MissingKey(key)
        if response.result:
            raise AgenticFanError("SMC_COMMAND_FAILED", "The SMC command failed.",
                              key=key, command=request.command, smc_result=response.result, smc_status=response.status)
        return response

    def info(self, key: str) -> KeyInfo:
        if key not in self.cache:
            data = SMCData(key=key_number(key), command=9)
            result = self._call(data, key).info
            if not 1 <= result.size <= 32:
                raise AgenticFanError("SMC_INVALID_SIZE", "SMC payload size is outside 1..32 bytes.", key=key, size=result.size)
            kind = result.kind.to_bytes(4, "big").decode("ascii", errors="replace")
            self.cache[key] = KeyInfo(kind, result.size, result.attributes)
        return self.cache[key]

    def read(self, key: str) -> bytes:
        info = self.info(key)
        data = SMCData(key=key_number(key), command=5)
        data.info.size = info.size
        return bytes(self._call(data, key).payload[:info.size])

    def write(self, key: str, raw: bytes):
        info = self.info(key)
        if len(raw) != info.size:
            raise AgenticFanError("SMC_INVALID_SIZE", "Write size does not match this key's metadata.", key=key)
        data = SMCData(key=key_number(key), command=6)
        data.info.size = len(raw)
        data.payload[:len(raw)] = raw
        self._call(data, key)

    def keys(self) -> list[str]:
        from .codecs import decode
        count = int(decode(self.info("#KEY"), self.read("#KEY")))
        if not 0 <= count <= 65536:
            raise AgenticFanError("SMC_INVALID_COUNT", "Unreasonable SMC key count.", count=count)
        # Exhaustive enumeration avoids assuming all firmware key tables are sorted.
        return [self._call(SMCData(command=8, index=i)).key.to_bytes(4, "big").decode("ascii", errors="replace")
                for i in range(count)]

    def close(self):
        if self.connection.value:
            self.close_service(self.connection)
            self.connection.value = 0
