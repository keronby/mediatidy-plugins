from __future__ import annotations

import asyncio
import contextvars
from pathlib import Path
from typing import Any, Callable

current: contextvars.ContextVar[Context | None] = contextvars.ContextVar("mt_invocation", default=None)


class SDKError(RuntimeError):
    def __init__(self, message: str, code: str = "OPERATION_FAILED", retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def _register(kind: str, name: str) -> Callable:
    def decorate(fn: Callable) -> Callable:
        if hasattr(fn, "__mt_handler__"):
            raise ValueError("A handler must have exactly one registration")
        setattr(fn, "__mt_handler__", (kind, name))
        return fn
    return decorate


def action(name: str) -> Callable:
    return _register("action", name)


def query(name: str) -> Callable:
    return _register("query", name)


def continuation(name: str) -> Callable:
    return _register("continuation", name)


class Context:
    """Host-supplied metadata is descriptive; authority stays in the host RPC closure."""
    def __init__(self, data: dict[str, Any], transport: Any, invocation: str):
        self.data, self._transport, self._invocation = data, transport, invocation
        self.cancelled = False
        from .clients import Client, AsyncClient
        self.mt = Client(self._call)
        self.amt = AsyncClient(self._acall)

    def __getattr__(self, name: str) -> Any:
        parts = name.split("_")
        camel = parts[0] + "".join(part.title() for part in parts[1:])
        if camel not in self.data:
            raise AttributeError(name)
        return self.data[camel]

    def _call(self, name: str, value: Any = None) -> Any:
        if self.cancelled:
            raise SDKError("Invocation cancelled", "CANCELLED")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SDKError("Use ctx.amt in async handlers", "ASYNC_CLIENT_REQUIRED")
        return self._transport.call(self._invocation, name, value or {})

    async def _acall(self, name: str, value: Any = None) -> Any:
        return await asyncio.to_thread(self._call, name, value)

    def download(self, ref: str) -> Path:
        return Path(self._call("files.download", {"ref": ref})["path"])

    def read_bytes(self, ref: str, limit: int = 8 * 1024 * 1024) -> bytes:
        if limit < 1 or limit > 8 * 1024 * 1024:
            raise SDKError("Binary memory limit exceeded", "LIMIT_EXCEEDED")
        path = self.download(ref)
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            raise SDKError("Binary memory limit exceeded", "LIMIT_EXCEEDED")
        return data

    async def adownload(self, ref: str) -> Path:
        return Path((await self._acall("files.download", {"ref": ref}))["path"])

    async def aread_bytes(self, ref: str, limit: int = 8 * 1024 * 1024) -> bytes:
        return await asyncio.to_thread(self.read_bytes, ref, limit)

    def upload(self, path: str | Path, destination: dict, *, idempotency_key: str,
               overwrite: bool = False, version: str = "") -> dict:
        return self._call("files.upload", {"path": str(path), "destination": destination,
                         "idempotencyKey": idempotency_key, "overwrite": overwrite, "version": version})

    def write_bytes(self, data: bytes, destination: dict, *, idempotency_key: str,
                    overwrite: bool = False, version: str = "") -> dict:
        if len(data) > 8 * 1024 * 1024:
            raise SDKError("Binary memory limit exceeded", "LIMIT_EXCEEDED")
        import uuid
        path = Path(self.work_dir) / (uuid.uuid4().hex + ".bin")
        path.write_bytes(data)
        return self.upload(path, destination, idempotency_key=idempotency_key, overwrite=overwrite, version=version)

    async def aupload(self, path: str | Path, destination: dict, *, idempotency_key: str,
                      overwrite: bool = False, version: str = "") -> dict:
        return await asyncio.to_thread(self.upload, path, destination, idempotency_key=idempotency_key, overwrite=overwrite, version=version)

    async def awrite_bytes(self, data: bytes, destination: dict, *, idempotency_key: str,
                           overwrite: bool = False, version: str = "") -> dict:
        return await asyncio.to_thread(self.write_bytes, data, destination, idempotency_key=idempotency_key, overwrite=overwrite, version=version)


class _Proxy:
    def __getattr__(self, name: str) -> Any:
        ctx = current.get()
        if ctx is None:
            raise SDKError("SDK calls require an active invocation", "CONTEXT_DENIED")
        return getattr(ctx.mt, name)


mt = _Proxy()


class Plugin:
    @property
    def mt(self) -> Any:
        return mt

    def validate_config(self, config: dict, ctx: Context) -> Any:
        return None

    def on_init(self, ctx: Context) -> Any:
        return None

    def on_stop(self, ctx: Context) -> Any:
        return None

    def get_page(self, ctx: Context) -> Any:
        from .ui import page
        return page()
