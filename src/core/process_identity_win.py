"""Pinned Windows process identities and user-bound update checkpoints.

No process discovery by name or port is permitted here. An update can transfer
only handles the outgoing launcher already owns, with creation time and image
checked again against a newly opened handle in the incoming launcher.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import subprocess
import threading


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_crypt = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
_kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel.GetProcessTimes.restype = wintypes.BOOL
_kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel.WaitForSingleObject.restype = wintypes.DWORD
_kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel.GetExitCodeProcess.restype = wintypes.BOOL
_kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel.TerminateProcess.restype = wintypes.BOOL
_kernel.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel.LocalFree.restype = wintypes.HLOCAL
_QUERY = 0x1000
_SYNCHRONIZE = 0x100000
_TERMINATE = 1
_TIMEOUT = 258


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: int
    image: str

    def payload(self) -> dict:
        return asdict(self)

    @classmethod
    def parse(cls, value: object) -> ProcessIdentity:
        if not isinstance(value, dict) or set(value) != {"pid", "created", "image"}:
            raise ValueError("Invalid process identity")
        if (type(value["pid"]) is not int or not 0 < value["pid"] < 2**32
                or type(value["created"]) is not int or not 0 < value["created"] < 2**64
                or not isinstance(value["image"], str) or len(value["image"]) > 32768
                or not Path(value["image"]).is_absolute()):
            raise ValueError("Invalid process identity")
        return cls(**value)


def _identity(handle: int, pid: int) -> ProcessIdentity:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    size = wintypes.DWORD(32768)
    image = ctypes.create_unicode_buffer(size.value)
    if not _kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
    return ProcessIdentity(pid, created, os.path.normcase(image.value))


def capture_process_identity(process: object) -> ProcessIdentity:
    """Capture from an owned handle, never from an unverified numeric PID."""
    handle = getattr(process, "_handle", None)
    if handle is None or process.poll() is not None:
        raise ValueError("Process is no longer owned or running")
    return _identity(int(handle), process.pid)


def current_process_identity() -> ProcessIdentity:
    handle = _kernel.OpenProcess(_QUERY, False, os.getpid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _identity(handle, os.getpid())
    finally:
        _kernel.CloseHandle(handle)


def identity_is_running(identity: ProcessIdentity) -> bool:
    handle = _kernel.OpenProcess(_QUERY | _SYNCHRONIZE, False, identity.pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # PID no longer exists
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        state = _kernel.WaitForSingleObject(handle, 0)
        if state == 0:
            return False
        if state != _TIMEOUT:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return _identity(handle, identity.pid) == identity
        except OSError:
            # The process can exit between the wait and image query. Windows
            # then reports ERROR_GEN_FAILURE for an otherwise valid handle.
            if _kernel.WaitForSingleObject(handle, 0) == 0:
                return False
            raise
    finally:
        _kernel.CloseHandle(handle)


class ReconnectedProcess:
    """The lifecycle/tracker Popen subset backed by one verified process handle."""

    def __init__(self, identity: ProcessIdentity):
        self._handle = None
        self._lock = threading.Lock()
        self.pid = identity.pid
        self.returncode = None
        handle = _kernel.OpenProcess(_QUERY | _SYNCHRONIZE | _TERMINATE, False, self.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if _identity(handle, self.pid) != identity or _kernel.WaitForSingleObject(handle, 0) != _TIMEOUT:
                raise ValueError("Process exited or its identity changed")
            self._handle = handle
        except BaseException:
            _kernel.CloseHandle(handle)
            raise

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            result = _kernel.WaitForSingleObject(self._handle, 0)
            if result == _TIMEOUT:
                return None
            if result != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = wintypes.DWORD()
            if not _kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.returncode = code.value
            return self.returncode

    def wait(self, timeout=None):
        milliseconds = 0xFFFFFFFF if timeout is None else min(0xFFFFFFFE, max(0, int(timeout * 1000)))
        if _kernel.WaitForSingleObject(self._handle, milliseconds) == _TIMEOUT:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        return self.poll()

    def terminate(self):
        with self._lock:
            if not _kernel.TerminateProcess(self._handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def __del__(self):
        handle = getattr(self, "_handle", None)
        if handle:
            try:
                _kernel.CloseHandle(handle)
            except (OSError, AttributeError):
                pass  # interpreter module teardown


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


for _name in ("CryptProtectData", "CryptUnprotectData"):
    _fn = getattr(_crypt, _name)
    _fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _fn.restype = wintypes.BOOL


def protect_checkpoint(data: bytes, *, decrypt: bool = False) -> bytes:
    """DPAPI authenticates the checkpoint and binds it to this Windows user."""
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = _Blob()
    function = _crypt.CryptUnprotectData if decrypt else _crypt.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        _kernel.LocalFree(ctypes.cast(destination.data, wintypes.HLOCAL))
