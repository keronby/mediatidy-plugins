"""Persistent Python worker; framed RPC never shares stdout/stderr."""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import inspect
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import traceback
import uuid
from typing import Any

from .core import Context, SDKError, current

MAX_MESSAGE = 4 * 1024 * 1024


class Transport:
    def __init__(self, sock: socket.socket):
        self.sock, self.reader = sock, sock.makefile("rb")
        self.lock = threading.Lock()
        self.pending: dict[str, concurrent.futures.Future] = {}
        self.pending_lock = threading.Lock()

    def send(self, frame: dict) -> None:
        data = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_MESSAGE:
            raise SDKError("RPC message exceeds limit", "LIMIT_EXCEEDED")
        with self.lock:
            self.sock.sendall(data)

    def call(self, invocation: str, name: str, value: Any) -> Any:
        seq = uuid.uuid4().hex
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self.pending_lock:
            self.pending[seq] = future
        try:
            self.send({"type": "call", "id": seq, "invocation": invocation, "method": name, "input": value})
            return future.result()
        finally:
            with self.pending_lock:
                self.pending.pop(seq, None)

    def response(self, frame: dict) -> None:
        with self.pending_lock:
            future = self.pending.get(frame["id"])
        if future and not future.done():
            if frame.get("error"):
                detail = frame.get("failure") or {}
                future.set_exception(SDKError(frame["error"], detail.get("code", "OPERATION_FAILED"), detail.get("retryable", False)))
            else:
                future.set_result(frame.get("result"))

    def close(self) -> None:
        with self.pending_lock:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(SDKError("Worker connection closed", "INTERRUPTED"))


class LogHandler(logging.Handler):
    def __init__(self, transport: Transport):
        super().__init__()
        self.transport = transport

    def emit(self, record: logging.LogRecord) -> None:
        ctx = current.get()
        try:
            self.transport.send({"type": "log", "invocation": ctx._invocation if ctx else "",
                                 "level": record.levelname.lower().replace("warning", "warn").replace("critical", "error"),
                                 "message": self.format(record)})
        except Exception:
            pass


def load_plugin(root: Path, manifest: dict) -> tuple[Any, dict[tuple[str, str], Any]]:
    sys.path.append(str(root))
    spec = importlib.util.spec_from_file_location("_mt_author_plugin", root / "plugin.py")
    if spec is None or spec.loader is None:
        raise ValueError("Missing plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plugin = module.Plugin()
    from .core import Plugin
    if not isinstance(plugin, Plugin):
        raise TypeError("plugin.py:Plugin must extend mt_plugin.Plugin")
    handlers: dict[tuple[str, str], Any] = {}
    for attr in dir(type(plugin)):
        registered = getattr(getattr(type(plugin), attr), "__mt_handler__", None)
        if registered:
            if registered in handlers:
                raise ValueError(f"Duplicate handler: {registered}")
            handlers[registered] = getattr(plugin, attr)
    declared = {("continuation" if a.get("kind") == "continuation" else "action", a["id"]) for a in manifest["actions"]}
    declared |= {("query", q["id"]) for q in manifest.get("queries", [])}
    if set(handlers) != declared:
        raise ValueError(f"Manifest/handler mismatch: declared={declared}, registered={set(handlers)}")
    return plugin, handlers


async def serve(transport: Transport, boot: dict) -> None:
    logging.basicConfig(level=logging.DEBUG, handlers=[LogHandler(transport)], force=True)
    plugin, handlers = load_plugin(Path(boot["root"]), boot["manifest"])
    transport.send({"type": "ready"})
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    def reader() -> None:
        try:
            while True:
                line = transport.reader.readline(MAX_MESSAGE + 1)
                if not line or len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                    break
                frame = json.loads(line)
                if frame["type"] == "response":
                    transport.response(frame)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, frame)
        finally:
            transport.close()
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "closed"})
    threading.Thread(target=reader, daemon=True, name="mt-rpc").start()
    locks = {name: asyncio.Lock() for name in ("business", "read", "lifecycle")}
    pools = {name: concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="mt-" + name) for name in locks}
    active: dict[str, tuple[asyncio.Task, Context]] = {}
    asynchronous: set[str] = set()

    async def invoke(frame: dict) -> None:
        envelope = frame["input"]
        kind, name = envelope["kind"], envelope.get("action", "")
        ctx = Context(envelope["ctx"], transport, frame["id"])
        active[frame["id"]] = (asyncio.current_task(), ctx)  # type: ignore[assignment]
        lane = "read" if kind in ("query", "page") else "lifecycle" if kind in ("init", "stop", "validateConfig") else "business"
        args: tuple[Any, ...]
        try:
            async with locks[lane]:
                if ctx.cancelled:
                    raise asyncio.CancelledError
                if kind in ("action", "continuation", "query"):
                    method, args = handlers[(kind, name)], (envelope.get("input") or {}, ctx)
                else:
                    method = getattr(plugin, {"init": "on_init", "stop": "on_stop", "validateConfig": "validate_config", "page": "get_page"}[kind])
                    args = (envelope.get("input") or {}, ctx) if kind == "validateConfig" else (ctx,)
                token = current.set(ctx)
                try:
                    if inspect.iscoroutinefunction(method):
                        asynchronous.add(frame["id"])
                        result = await method(*args)
                    else:
                        result = await loop.run_in_executor(pools[lane], contextvars_copy(method, args))
                        if inspect.isawaitable(result):
                            asynchronous.add(frame["id"])
                            result = await result
                    transport.send({"type": "result", "id": frame["id"], "result": result})
                finally:
                    current.reset(token)
        except asyncio.CancelledError:
            transport.send({"type": "result", "id": frame["id"], "error": "Invocation cancelled"})
        except SDKError as error:
            transport.send({"type": "result", "id": frame["id"], "error": f"{error.code}: {error}"[:2000]})
        except BaseException:
            transport.send({"type": "result", "id": frame["id"], "error": "插件执行失败，请查看日志", "diagnostic": ("".join(traceback.format_exception_only(sys.exception())) + traceback.format_exc())[:32000]})
        finally:
            ctx.cancelled = True
            asynchronous.discard(frame["id"])
            active.pop(frame["id"], None)

    while True:
        frame = await queue.get()
        if frame["type"] == "closed":
            os._exit(0)
        elif frame["type"] == "invoke":
            asyncio.create_task(invoke(frame))
        elif frame["type"] == "cancel":
            item = active.get(frame["id"])
            if item:
                item[1].cancelled = True
                # Sync handlers retain their lane until they actually return. The host
                # terminates the process tree after the cancellation grace period.
                if frame["id"] in asynchronous:
                    item[0].cancel()


def contextvars_copy(method: Any, args: tuple) -> Any:
    import contextvars
    copied = contextvars.copy_context()
    return lambda: copied.run(method, *args)


def main() -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("MediaTidy plugins require Python 3.12")
    boot = json.loads(sys.stdin.buffer.readline(MAX_MESSAGE))
    sock = socket.create_connection(("127.0.0.1", boot["port"]), timeout=30)
    sock.settimeout(None)
    transport = Transport(sock)
    transport.send({"type": "hello", "id": boot["token"]})
    try:
        asyncio.run(serve(transport, boot))
    except BaseException:
        transport.send({"type": "fatal", "error": "插件导入失败", "diagnostic": ("".join(traceback.format_exception_only(sys.exception())) + traceback.format_exc())[:32000]})
        raise


if __name__ == "__main__":
    main()
