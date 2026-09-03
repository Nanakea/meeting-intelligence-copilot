"""Windows user-scoped secret and data protection adapters."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol


class DataProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class SecretStore(Protocol):
    def put(self, target: str, secret: str) -> None: ...

    def get(self, target: str) -> str | None: ...

    def delete(self, target: str) -> bool: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDpapiProtector:
    """DPAPI without LOCAL_MACHINE scope, so another Windows user cannot decrypt."""

    _UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI is required for the context index")
        self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p
        entropy = b"meeting-intelligence-context-v1"
        self._entropy_buffer = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy)
        self._entropy = _DataBlob(len(entropy), self._entropy_buffer)

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, object]:
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        return _DataBlob(len(value), buffer), buffer

    def protect(self, value: bytes) -> bytes:
        source, source_buffer = self._blob(value)
        protected = _DataBlob()
        _ = source_buffer
        if not self._crypt32.CryptProtectData(
            ctypes.byref(source),
            "Meeting Intelligence context",
            ctypes.byref(self._entropy),
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(protected),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(protected.data, protected.size)
        finally:
            self._kernel32.LocalFree(protected.data)

    def unprotect(self, value: bytes) -> bytes:
        source, source_buffer = self._blob(value)
        clear = _DataBlob()
        description = wintypes.LPWSTR()
        _ = source_buffer
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            ctypes.byref(self._entropy),
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(clear),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(clear.data, clear.size)
        finally:
            if description:
                self._kernel32.LocalFree(description)
            self._kernel32.LocalFree(clear.data)


class _Credential(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.DWORD),
        ("type", wintypes.DWORD),
        ("target_name", wintypes.LPWSTR),
        ("comment", wintypes.LPWSTR),
        ("last_written", wintypes.FILETIME),
        ("blob_size", wintypes.DWORD),
        ("blob", ctypes.POINTER(ctypes.c_ubyte)),
        ("persist", wintypes.DWORD),
        ("attribute_count", wintypes.DWORD),
        ("attributes", ctypes.c_void_p),
        ("target_alias", wintypes.LPWSTR),
        ("user_name", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    _GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2
    _NOT_FOUND = 1168

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Credential Manager is required")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        pointer = ctypes.POINTER(_Credential)
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(pointer),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]

    def put(self, target: str, secret: str) -> None:
        encoded = secret.encode("utf-16-le")
        if not encoded or len(encoded) > 2_560:
            raise ValueError("credential secret must be 1-1280 characters")
        buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _Credential(
            type=self._GENERIC,
            target_name=target,
            blob_size=len(encoded),
            blob=buffer,
            persist=self._PERSIST_LOCAL_MACHINE,
            user_name="Meeting Intelligence Copilot",
        )
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def get(self, target: str) -> str | None:
        pointer = ctypes.POINTER(_Credential)()
        if not self._advapi.CredReadW(
            target, self._GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == self._NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            credential = pointer.contents
            return ctypes.string_at(credential.blob, credential.blob_size).decode(
                "utf-16-le"
            )
        finally:
            self._advapi.CredFree(pointer)

    def delete(self, target: str) -> bool:
        if self._advapi.CredDeleteW(target, self._GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == self._NOT_FOUND:
            return False
        raise ctypes.WinError(error)
