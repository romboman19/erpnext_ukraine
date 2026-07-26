from __future__ import annotations

# FTP is an explicit legacy transport option; prefer FTPS/SFTP for new endpoints.
import ftplib  # nosec B402
import hashlib
import io
import json
import posixpath
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any

from erpnext_ua.ecommerce.base.transport.http import AmbiguousTransportError
from erpnext_ua.integrations.utils.validation import validate_hostname

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_LISTED_FILES = 10_000


class FileDeliveryTransport:
    """FTP/FTPS/SFTP transport for administrator-configured endpoints."""

    def __init__(self, endpoint: Any):
        self.endpoint = endpoint
        self.protocol = str(_value(endpoint, "protocol", "") or "").strip().upper()
        if self.protocol not in {"FTP", "FTPS", "SFTP"}:
            raise ValueError("File delivery protocol must be FTP, FTPS or SFTP")
        self.host = validate_hostname(
            str(_value(endpoint, "host", "") or ""),
            "File Delivery Endpoint",
        )
        self.port = int(_value(endpoint, "port", 0) or _default_port(self.protocol))
        if not 1 <= self.port <= 65535:
            raise ValueError("File delivery port must be between 1 and 65535")
        self.username = str(_value(endpoint, "username", "") or "").strip()
        self.base_path = _validate_base_path(str(_value(endpoint, "base_path", "/") or "/"))
        self.passive_mode = bool(_value(endpoint, "passive_mode", 1))
        self.password = _password(endpoint, "password")
        self.ssh_key = _password(endpoint, "ssh_key")
        if not self.username:
            raise ValueError("File delivery username is required")
        if self.protocol in {"FTP", "FTPS"} and not self.password:
            raise ValueError("FTP/FTPS password is required")
        if self.protocol == "SFTP" and not (self.password or self.ssh_key):
            raise ValueError("SFTP password or SSH key is required")

    def test_connection(self) -> dict:
        with self._connection() as connection:
            if self.protocol == "SFTP":
                connection.stat(self.base_path)
            else:
                connection.cwd(self.base_path)
        return {"ok": True, "protocol": self.protocol, "host": self.host}

    def list_files(self, suffix: str | None = None) -> list[str]:
        with self._connection() as connection:
            names = (
                connection.listdir(self.base_path)
                if self.protocol == "SFTP"
                else connection.nlst(self.base_path)
            )
        clean = []
        for remote_name in names:
            name = posixpath.basename(str(remote_name).rstrip("/"))
            if not name or name.startswith(".") or name.endswith(".meta.json"):
                continue
            if suffix and not name.lower().endswith(str(suffix).lower()):
                continue
            clean.append(_validate_filename(name))
            if len(clean) > MAX_LISTED_FILES:
                raise ValueError("File delivery endpoint contains too many files")
        return sorted(set(clean))

    def download(self, remote_name: str) -> bytes:
        path = self._path(remote_name)
        buffer = io.BytesIO()
        with self._connection() as connection:
            if self.protocol == "SFTP":
                with connection.open(path, "rb") as remote:
                    while chunk := remote.read(64 * 1024):
                        _write_bounded(buffer, chunk)
            else:
                connection.retrbinary(f"RETR {path}", lambda chunk: _write_bounded(buffer, chunk))
        return buffer.getvalue()

    def upload(self, remote_name: str, content: bytes, *, idempotency_key: str) -> dict:
        """Upload once and persist a checksum sidecar for immutable retries."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("File upload requires an idempotency key")
        payload = bytes(content)
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("Ecommerce upload file is too large")
        name = _validate_filename(remote_name)
        digest = hashlib.sha256(payload).hexdigest()
        metadata = json.dumps(
            {"idempotency_key": key, "sha256": digest, "size": len(payload)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        final_path = self._path(name)
        meta_path = self._path(f".{name}.meta.json")
        temp_suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        temp_path = self._path(f".{name}.{temp_suffix}.part")
        temp_meta_path = f"{temp_path}.meta"
        try:
            with self._connection() as connection:
                existing_meta = self._read_optional(connection, meta_path)
                if existing_meta is not None:
                    self._assert_same_upload(existing_meta, key, digest, len(payload))
                    if not self._exists(connection, final_path):
                        raise AmbiguousTransportError("Upload metadata exists but the data file is missing")
                    return {"ok": True, "idempotent": True, "sha256": digest, "remote_name": name}
                if self._exists(connection, final_path):
                    raise AmbiguousTransportError("Remote file exists without idempotency metadata")
                self._write(connection, temp_path, payload)
                self._rename(connection, temp_path, final_path)
                self._write(connection, temp_meta_path, metadata)
                self._rename(connection, temp_meta_path, meta_path)
        except AmbiguousTransportError:
            raise
        except (ftplib.Error, OSError) as exc:
            raise AmbiguousTransportError("File upload outcome is unknown") from exc
        return {"ok": True, "idempotent": False, "sha256": digest, "remote_name": name}

    def publish(self, remote_name: str, content: bytes, *, idempotency_key: str) -> dict:
        """Atomically publish a replaceable named feed.

        ``upload`` is immutable and is used for versioned assets such as photos.
        A catalog feed normally has a stable filename, so a new immutable
        operation key may replace an older version. Reusing the *same* key with
        different content remains forbidden.
        """
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("File publish requires an idempotency key")
        payload = bytes(content)
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("Ecommerce publish file is too large")
        name = _validate_filename(remote_name)
        digest = hashlib.sha256(payload).hexdigest()
        metadata_values = {"idempotency_key": key, "sha256": digest, "size": len(payload)}
        metadata = json.dumps(metadata_values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        final_path = self._path(name)
        meta_path = self._path(f".{name}.meta.json")
        temp_suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        temp_path = self._path(f".{name}.{temp_suffix}.part")
        temp_meta_path = f"{temp_path}.meta"
        try:
            with self._connection() as connection:
                existing_meta = self._read_optional(connection, meta_path)
                if existing_meta is not None:
                    saved = self._load_metadata(existing_meta)
                    if saved.get("idempotency_key") == key:
                        if saved != metadata_values:
                            raise ValueError(
                                "Idempotency key was already used for different published content"
                            )
                        if not self._exists(connection, final_path):
                            raise AmbiguousTransportError(
                                "Publish metadata exists but the data file is missing"
                            )
                        return {
                            "ok": True,
                            "idempotent": True,
                            "sha256": digest,
                            "remote_name": name,
                        }
                self._delete_optional(connection, temp_path)
                self._delete_optional(connection, temp_meta_path)
                self._write(connection, temp_path, payload)
                self._write(connection, temp_meta_path, metadata)
                self._replace_pair(
                    connection,
                    data_temp=temp_path,
                    data_final=final_path,
                    meta_temp=temp_meta_path,
                    meta_final=meta_path,
                    suffix=temp_suffix,
                )
        except (AmbiguousTransportError, ValueError):
            raise
        except (ftplib.Error, OSError) as exc:
            raise AmbiguousTransportError("File publish outcome is unknown") from exc
        return {"ok": True, "idempotent": False, "sha256": digest, "remote_name": name}

    def delete(self, remote_name: str) -> dict:
        """Idempotently remove a fully processed inbound file and its sidecar."""
        name = _validate_filename(remote_name)
        path = self._path(name)
        meta_path = self._path(f".{name}.meta.json")
        try:
            with self._connection() as connection:
                deleted = self._delete_optional(connection, path)
                self._delete_optional(connection, meta_path)
        except (ftplib.Error, OSError) as exc:
            raise AmbiguousTransportError("File deletion outcome is unknown") from exc
        return {"ok": True, "deleted": deleted, "remote_name": name}

    @contextmanager
    def _connection(self):
        if self.protocol == "SFTP":
            try:
                import paramiko
            except ImportError as exc:
                raise RuntimeError("SFTP support requires the paramiko package") from exc
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            private_key = _load_private_key(paramiko, self.ssh_key) if self.ssh_key else None
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password or None,
                pkey=private_key,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
            sftp = client.open_sftp()
            try:
                yield sftp
            finally:
                sftp.close()
                client.close()
            return

        if self.protocol == "FTPS":  # noqa: SIM108
            connection = ftplib.FTP_TLS(timeout=30)
        else:
            # Plain FTP is opt-in through an administrator-configured endpoint.
            connection = ftplib.FTP(timeout=30)  # nosec B321
        connection.connect(self.host, self.port)
        connection.login(self.username, self.password)
        connection.set_pasv(self.passive_mode)
        if self.protocol == "FTPS":
            connection.prot_p()
        try:
            yield connection
        finally:
            try:
                connection.quit()
            except (ftplib.Error, OSError):
                connection.close()

    def _path(self, remote_name: str) -> str:
        return posixpath.join(self.base_path, _validate_filename(remote_name))

    def _exists(self, connection, path: str) -> bool:
        if self.protocol == "SFTP":
            try:
                connection.stat(path)
                return True
            except OSError:
                return False
        try:
            connection.size(path)
            return True
        except ftplib.Error:
            return False

    def _read_optional(self, connection, path: str) -> bytes | None:
        if not self._exists(connection, path):
            return None
        buffer = io.BytesIO()
        if self.protocol == "SFTP":
            with connection.open(path, "rb") as remote:
                _write_bounded(buffer, remote.read(64 * 1024))
        else:
            connection.retrbinary(f"RETR {path}", lambda chunk: _write_bounded(buffer, chunk))
        return buffer.getvalue()

    def _write(self, connection, path: str, content: bytes) -> None:
        if self.protocol == "SFTP":
            with connection.open(path, "wb") as remote:
                remote.write(content)
        else:
            connection.storbinary(f"STOR {path}", io.BytesIO(content))

    def _rename(self, connection, source: str, target: str) -> None:
        connection.rename(source, target)

    def _replace_pair(
        self,
        connection,
        *,
        data_temp: str,
        data_final: str,
        meta_temp: str,
        meta_final: str,
        suffix: str,
    ) -> None:
        if self.protocol == "SFTP" and callable(getattr(connection, "posix_rename", None)):
            connection.posix_rename(data_temp, data_final)
            connection.posix_rename(meta_temp, meta_final)
            return

        data_backup = f"{data_temp}.{suffix}.bak"
        meta_backup = f"{meta_temp}.{suffix}.bak"
        self._delete_optional(connection, data_backup)
        self._delete_optional(connection, meta_backup)
        data_had_previous = self._exists(connection, data_final)
        meta_had_previous = self._exists(connection, meta_final)
        if data_had_previous:
            self._rename(connection, data_final, data_backup)
        if meta_had_previous:
            self._rename(connection, meta_final, meta_backup)
        try:
            self._rename(connection, data_temp, data_final)
            self._rename(connection, meta_temp, meta_final)
        except (ftplib.Error, OSError):
            self._delete_optional(connection, data_final)
            self._delete_optional(connection, meta_final)
            if data_had_previous and self._exists(connection, data_backup):
                self._rename(connection, data_backup, data_final)
            if meta_had_previous and self._exists(connection, meta_backup):
                self._rename(connection, meta_backup, meta_final)
            raise
        self._delete_optional(connection, data_backup)
        self._delete_optional(connection, meta_backup)

    def _delete_optional(self, connection, path: str) -> bool:
        if not self._exists(connection, path):
            return False
        connection.remove(path) if self.protocol == "SFTP" else connection.delete(path)
        return True

    @staticmethod
    def _assert_same_upload(metadata: bytes, key: str, digest: str, size: int) -> None:
        saved = FileDeliveryTransport._load_metadata(metadata)
        if saved != {"idempotency_key": key, "sha256": digest, "size": size}:
            raise ValueError("Idempotency key was already used for a different file upload")

    @staticmethod
    def _load_metadata(metadata: bytes) -> dict:
        try:
            saved = json.loads(metadata.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AmbiguousTransportError("Remote upload metadata is invalid") from exc
        if not isinstance(saved, dict):
            raise AmbiguousTransportError("Remote upload metadata has an unexpected shape")
        return saved


def _value(source: Any, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    getter = getattr(source, "get", None)
    return getter(key, default) if callable(getter) else getattr(source, key, default)


def _password(endpoint: Any, fieldname: str) -> str:
    getter = getattr(endpoint, "get_password", None)
    if callable(getter):
        return str(getter(fieldname, raise_exception=False) or "")
    return str(_value(endpoint, fieldname, "") or "")


def _default_port(protocol: str) -> int:
    return 22 if protocol == "SFTP" else 21


def _validate_base_path(path: str) -> str:
    normalized = str(path or "/").strip().replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if ".." in parts or "\0" in normalized:
        raise ValueError("File delivery base path cannot traverse directories")
    return "/" + normalized.strip("/") if normalized.strip("/") else "/"


def _validate_filename(filename: str) -> str:
    name = str(filename or "").strip()
    if not name or name in {".", ".."} or posixpath.basename(name) != name or "\\" in name or "\0" in name:
        raise ValueError("File delivery name must be a plain filename")
    if len(name) > 240:
        raise ValueError("File delivery name is too long")
    return name


def _write_bounded(buffer: io.BytesIO, chunk: bytes) -> None:
    if buffer.tell() + len(chunk) > MAX_FILE_BYTES:
        raise ValueError("Ecommerce file is too large")
    buffer.write(chunk)


def _load_private_key(paramiko, key_text: str):
    errors = []
    key_text = key_text.replace("\\n", "\n")
    key_types = [paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey]
    for key_type in key_types:
        try:
            return key_type.from_private_key(io.StringIO(key_text))
        except (paramiko.SSHException, ValueError) as exc:
            errors.append(exc)
    raise ValueError("SFTP SSH key is invalid") from errors[-1]
