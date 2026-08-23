"""Windows DPAPI wrapper used for ReplayCapsule blobs."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class SecretProtectionError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def protect(plaintext: bytes, *, entropy: bytes = b"skilltree-replay/v1") -> bytes:
    if sys.platform != "win32":
        raise SecretProtectionError("dpapi_unavailable")
    return _crypt("CryptProtectData", plaintext, entropy)


def unprotect(ciphertext: bytes, *, entropy: bytes = b"skilltree-replay/v1") -> bytes:
    if sys.platform != "win32":
        raise SecretProtectionError("dpapi_unavailable")
    return _crypt("CryptUnprotectData", ciphertext, entropy)


def _crypt(name: str, payload: bytes, entropy: bytes) -> bytes:
    if not isinstance(payload, bytes) or not payload:
        raise SecretProtectionError("invalid_blob")
    api = ctypes.windll.crypt32
    function = getattr(api, name)
    function.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    source = (ctypes.c_byte * len(payload)).from_buffer_copy(payload)
    entropy_buffer = (ctypes.c_byte * len(entropy)).from_buffer_copy(entropy)
    source_blob = _Blob(len(payload), source)
    entropy_blob = _Blob(len(entropy), entropy_buffer)
    output = _Blob()
    if not function(ctypes.byref(source_blob), "SkillTree ReplayCapsule", ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(output)):
        raise SecretProtectionError("dpapi_failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
