"""OS-backed secret storage and authenticated encrypted content storage."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .schema import DatabaseTarget, ensure_schema


class SecretBackend(Protocol):
    """Opaque byte storage.  Implementations must protect values at rest."""

    def set(self, target: str, value: bytes) -> None:
        ...

    def get(self, target: str) -> Optional[bytes]:
        ...

    def delete(self, target: str) -> None:
        ...


class SecretStoreUnavailableError(RuntimeError):
    pass


class InMemorySecretBackend:
    """Explicit, process-only backend for unit tests.

    It is never selected automatically and should not be used by application
    wiring.  This explicit injection requirement prevents a plaintext fallback on
    platforms where Windows credential protection is unavailable.
    """

    def __init__(self) -> None:
        self._values: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def set(self, target: str, value: bytes) -> None:
        with self._lock:
            self._values[target] = bytes(value)

    def get(self, target: str) -> Optional[bytes]:
        with self._lock:
            value = self._values.get(target)
            return None if value is None else bytes(value)

    def delete(self, target: str) -> None:
        with self._lock:
            self._values.pop(target, None)


if os.name == "nt":
    from ctypes import wintypes

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]


class WindowsCredentialBackend:
    """Generic credentials protected by Windows Credential Manager."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168
    _MAX_BLOB_BYTES = 2560

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretStoreUnavailableError("Windows Credential Manager is Windows-only")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        credential_pointer = ctypes.POINTER(_CREDENTIALW)
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(credential_pointer),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    @staticmethod
    def _raise_last_error() -> None:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))

    def set(self, target: str, value: bytes) -> None:
        value = bytes(value)
        if len(value) > self._MAX_BLOB_BYTES:
            raise ValueError("Credential Manager secret exceeds 2560-byte limit")
        if value:
            buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
            pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        else:
            buffer = None
            pointer = ctypes.POINTER(ctypes.c_ubyte)()
        credential = _CREDENTIALW()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(value)
        credential.CredentialBlob = pointer
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "lilies"
        # Keep the buffer alive until the native call has returned.
        _ = buffer
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            self._raise_last_error()

    def get(self, target: str) -> Optional[bytes]:
        credential_pointer = ctypes.POINTER(_CREDENTIALW)()
        ok = self._advapi.CredReadW(
            target,
            self._CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == self._ERROR_NOT_FOUND:
                return None
            self._raise_last_error()
        try:
            credential = credential_pointer.contents
            if credential.CredentialBlobSize == 0:
                return b""
            return ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
        finally:
            self._advapi.CredFree(credential_pointer)

    def delete(self, target: str) -> None:
        if self._advapi.CredDeleteW(target, self._CRED_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != self._ERROR_NOT_FOUND:
            self._raise_last_error()


class DpapiFileSecretBackend:
    """DPAPI-encrypted file backend for larger Windows-only secrets.

    Filenames contain only a SHA-256 digest of the logical target.  File contents
    are exactly a DPAPI blob; no plaintext or reversible metadata is written.
    """

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self, root: Union[str, Path]) -> None:
        if os.name != "nt":
            raise SecretStoreUnavailableError("DPAPI is Windows-only")
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DATA_BLOB)
        self._crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _path_name(target: str) -> str:
        return hashlib.sha256(target.encode("utf-8")).hexdigest() + ".dpapi"

    def _path(self, target: str) -> Path:
        return self._root / self._path_name(target)

    @staticmethod
    def _input_blob(value: bytes) -> tuple[Any, Any]:
        if value:
            buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
            pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        else:
            buffer = None
            pointer = ctypes.POINTER(ctypes.c_ubyte)()
        return _DATA_BLOB(len(value), pointer), buffer

    @staticmethod
    def _native_error() -> OSError:
        error = ctypes.get_last_error()
        return OSError(error, ctypes.FormatError(error))

    def _protect(self, value: bytes) -> bytes:
        source, buffer = self._input_blob(value)
        output = _DATA_BLOB()
        _ = buffer
        if not self._crypt32.CryptProtectData(
            ctypes.byref(source),
            "Lilies connector secret",
            None,
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise self._native_error()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        source, buffer = self._input_blob(value)
        output = _DATA_BLOB()
        _ = buffer
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise self._native_error()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def set(self, target: str, value: bytes) -> None:
        path = self._path(target)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(self._protect(bytes(value)))
        os.replace(str(temporary), str(path))

    def get(self, target: str) -> Optional[bytes]:
        path = self._path(target)
        if not path.exists():
            return None
        return self._unprotect(path.read_bytes())

    def delete(self, target: str) -> None:
        try:
            self._path(target).unlink()
        except FileNotFoundError:
            pass


class SecretStore:
    """Namespaced secrets with a secure Windows default and no fallback."""

    def __init__(
        self,
        namespace: str = "lilies-in-the-box",
        *,
        backend: Optional[SecretBackend] = None,
    ) -> None:
        namespace = namespace.strip()
        if not namespace:
            raise ValueError("Secret namespace must not be empty")
        if backend is None:
            if os.name != "nt":
                raise SecretStoreUnavailableError(
                    "A secure secret backend must be explicitly injected off Windows"
                )
            backend = WindowsCredentialBackend()
        self._namespace = namespace
        self._backend = backend

    def _target(self, key: str) -> str:
        key = key.strip()
        if not key or "\x00" in key:
            raise ValueError("Secret key must be non-empty and contain no NUL")
        return "%s/%s" % (self._namespace, key)

    def set_bytes(self, key: str, value: bytes) -> None:
        self._backend.set(self._target(key), bytes(value))

    def get_bytes(self, key: str) -> Optional[bytes]:
        return self._backend.get(self._target(key))

    def set_text(self, key: str, value: str) -> None:
        self.set_bytes(key, value.encode("utf-8"))

    def get_text(self, key: str) -> Optional[str]:
        value = self.get_bytes(key)
        return None if value is None else value.decode("utf-8")

    def delete(self, key: str) -> None:
        self._backend.delete(self._target(key))

    # Clear aliases used by connector call sites.
    set_secret = set_text
    get_secret = get_text
    delete_secret = delete


@dataclass(frozen=True)
class VaultEntry:
    content_id: str
    namespace: str
    content: bytes
    metadata: Mapping[str, Any]
    created_at: str
    updated_at: str


class EncryptedContentVault:
    """AES-256-GCM envelopes whose master key lives only in ``SecretStore``."""

    _PREFIX = b"LILIES-VAULT\x01"
    _NONCE_BYTES = 12

    def __init__(
        self,
        secret_store: SecretStore,
        *,
        key_name: str = "encrypted-content-v1/master-key",
        database: Optional[DatabaseTarget] = None,
    ) -> None:
        self._secret_store = secret_store
        self._key_name = key_name
        self._lock = threading.RLock()
        self._connection = ensure_schema(database) if database is not None else None

    def _master_key(self) -> bytes:
        with self._lock:
            key = self._secret_store.get_bytes(self._key_name)
            if key is None:
                key = AESGCM.generate_key(bit_length=256)
                self._secret_store.set_bytes(self._key_name, key)
            if len(key) != 32:
                raise ValueError("Vault master key has an invalid length")
            return key

    @staticmethod
    def _aad_bytes(associated_data: Union[str, bytes]) -> bytes:
        return (
            associated_data.encode("utf-8")
            if isinstance(associated_data, str)
            else bytes(associated_data)
        )

    def encrypt(
        self, plaintext: Union[str, bytes], *, associated_data: Union[str, bytes] = b""
    ) -> bytes:
        raw = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
        nonce = os.urandom(self._NONCE_BYTES)
        ciphertext = AESGCM(self._master_key()).encrypt(
            nonce, raw, self._aad_bytes(associated_data)
        )
        return self._PREFIX + nonce + ciphertext

    def decrypt(
        self, envelope: bytes, *, associated_data: Union[str, bytes] = b""
    ) -> bytes:
        envelope = bytes(envelope)
        if not envelope.startswith(self._PREFIX):
            raise ValueError("Unsupported encrypted content envelope")
        offset = len(self._PREFIX)
        nonce = envelope[offset : offset + self._NONCE_BYTES]
        ciphertext = envelope[offset + self._NONCE_BYTES :]
        if len(nonce) != self._NONCE_BYTES or len(ciphertext) < 16:
            raise ValueError("Truncated encrypted content envelope")
        return AESGCM(self._master_key()).decrypt(
            nonce, ciphertext, self._aad_bytes(associated_data)
        )

    def seal_text(self, plaintext: str, *, associated_data: str = "") -> str:
        return base64.urlsafe_b64encode(
            self.encrypt(plaintext, associated_data=associated_data)
        ).decode("ascii")

    def open_text(self, token: str, *, associated_data: str = "") -> str:
        return self.decrypt(
            base64.urlsafe_b64decode(token.encode("ascii")),
            associated_data=associated_data,
        ).decode("utf-8")

    def _require_database(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("This vault was created without a database")
        return self._connection

    @staticmethod
    def _record_aad(namespace: str, content_id: str) -> str:
        return "connector-content\x00%s\x00%s" % (namespace, content_id)

    def put(
        self,
        content_id: str,
        content: Union[str, bytes],
        *,
        namespace: str = "default",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        connection = self._require_database()
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        protected_record = json.dumps(
            {
                "content": base64.b64encode(raw).decode("ascii"),
                "metadata": dict(metadata or {}),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        envelope = self.encrypt(
            protected_record, associated_data=self._record_aad(namespace, content_id)
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            connection.execute(
                """
                INSERT INTO connector_encrypted_content(
                    content_id, namespace, ciphertext, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, content_id) DO UPDATE SET
                    ciphertext=excluded.ciphertext,
                    updated_at=excluded.updated_at
                """,
                (content_id, namespace, envelope, now, now),
            )
            connection.commit()

    def get(self, content_id: str, *, namespace: str = "default") -> Optional[VaultEntry]:
        connection = self._require_database()
        row = connection.execute(
            """
            SELECT namespace, ciphertext, created_at, updated_at
            FROM connector_encrypted_content
            WHERE content_id = ? AND namespace = ?
            """,
            (content_id, namespace),
        ).fetchone()
        if row is None:
            return None
        record = json.loads(
            self.decrypt(
                row[1], associated_data=self._record_aad(namespace, content_id)
            ).decode("utf-8")
        )
        return VaultEntry(
            content_id=content_id,
            namespace=namespace,
            content=base64.b64decode(record["content"]),
            metadata=record.get("metadata", {}),
            created_at=row[2],
            updated_at=row[3],
        )

    def delete(self, content_id: str, *, namespace: str = "default") -> bool:
        connection = self._require_database()
        with self._lock:
            cursor = connection.execute(
                "DELETE FROM connector_encrypted_content WHERE content_id=? AND namespace=?",
                (content_id, namespace),
            )
            connection.commit()
            return cursor.rowcount > 0


# Acronym-preserving compatibility name for application wiring.
DPAPISecretBackend = DpapiFileSecretBackend
WindowsCredentialManagerBackend = WindowsCredentialBackend
