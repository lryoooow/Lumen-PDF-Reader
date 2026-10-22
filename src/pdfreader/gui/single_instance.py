"""单实例：第二次启动把文件转交给已运行的窗口。

用 Qt 的本地套接字（Windows 命名管道）做进程间通信，不依赖第三方库：

* 先尝试连接同名服务；连上了说明已有实例，把要打开的文件发过去后退出；
* 连不上就清理遗留的套接字名并开始监听，收到消息时发 :attr:`messageReceived`。

服务名带上用户名，避免同一台机器上不同用户互相抢占。消息是一行 UTF-8 JSON
（``{"paths": [...], "cwd": "..."}``），路径保持原样，由接收方结合 ``cwd`` 解析相对路径。
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

__all__ = ["SingleInstance", "default_server_name"]

_logger = logging.getLogger("pdfreader")

_CONNECT_TIMEOUT_MS = 300
_WRITE_TIMEOUT_MS = 500
_MAX_MESSAGE_BYTES = 64 * 1024


def default_server_name() -> str:
    """当前用户的本地套接字名（不同用户互不干扰）。"""
    try:
        user = getpass.getuser()
    except Exception:  # 极少数环境拿不到用户名，退回固定值
        _logger.debug("读取用户名失败，使用默认单实例名", exc_info=True)
        user = "default"
    digest = hashlib.sha256(user.encode("utf-8", "replace")).hexdigest()[:12]
    return f"Lumen-PDF-Reader-{digest}"


class SingleInstance(QObject):
    """单实例协调器（每个进程一个）。

    用法::

        instance = SingleInstance()
        if not instance.try_start():
            instance.send_open(paths, str(Path.cwd()))
            return 0
        instance.messageReceived.connect(window.handle_second_instance)
    """

    #: 收到另一个实例的请求：(绝对/相对路径列表, 对方工作目录)
    messageReceived = Signal(list, str)

    def __init__(self, key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key or default_server_name()
        self._server: QLocalServer | None = None
        self._socket: QLocalSocket | None = None
        self._buffers: dict[QLocalSocket, bytearray] = {}

    @property
    def key(self) -> str:
        return self._key

    def try_start(self) -> bool:
        """尝试成为主实例。返回 False 表示已有实例在运行（:meth:`send_open` 可用）。"""
        probe = QLocalSocket(self)
        probe.connectToServer(self._key)
        if probe.waitForConnected(_CONNECT_TIMEOUT_MS):
            self._socket = probe
            return False
        probe.abort()
        probe.deleteLater()
        # 上次异常退出可能留下同名套接字，连接失败后清理再监听
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self._key):
            _logger.warning("单实例监听失败：%s", server.errorString())
            server.deleteLater()
            return True  # 监听失败不阻止本次启动（最坏情况是允许多开）
        self._server = server
        return True

    def send_open(self, paths: list[str], cwd: str) -> bool:
        """把要打开的文件发给已运行的实例。返回是否发送成功。"""
        socket = self._socket
        if socket is None:
            return False
        payload = json.dumps(
            {"paths": [str(p) for p in paths], "cwd": str(cwd)}, ensure_ascii=False
        ).encode("utf-8")
        if socket.write(payload + b"\n") < 0:
            _logger.warning("单实例消息写入失败：%s", socket.errorString())
            return False
        socket.flush()
        # Windows 命名管道的写入由事件分发器驱动：只调用 flush / waitForBytesWritten
        # 不会推进（实测 bytesToWrite 一直为 30），必须边处理事件边等。
        deadline = time.monotonic() + _WRITE_TIMEOUT_MS / 1000
        while socket.bytesToWrite() > 0 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            socket.flush()
            if socket.bytesToWrite() > 0:
                QThread.msleep(2)
        ok = socket.bytesToWrite() == 0
        socket.disconnectFromServer()
        deadline = time.monotonic() + _WRITE_TIMEOUT_MS / 1000
        while (
            socket.state() != QLocalSocket.LocalSocketState.UnconnectedState
            and time.monotonic() < deadline
        ):
            QCoreApplication.processEvents()
            QThread.msleep(2)
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.abort()
        return ok

    def close(self) -> None:
        """停止监听 / 断开连接（退出或测试清理时调用）。"""
        for socket in list(self._buffers):
            self._drop_socket(socket)
        if self._socket is not None:
            self._socket.abort()
            self._socket.deleteLater()
            self._socket = None
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            # 用有 QObject 接收者的槽，避免 lambda 捕获 socket 自身：Qt 析构连接时
            # 释放这样的闭包可能重入 Python 包装对象的析构，触发原生 pure virtual call。
            socket.readyRead.connect(self._on_ready_read)
            socket.disconnected.connect(self._on_disconnected)

    @Slot()
    def _on_ready_read(self) -> None:
        socket = self.sender()
        if not isinstance(socket, QLocalSocket):
            return
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > _MAX_MESSAGE_BYTES:
            _logger.warning("单实例消息过长（%d 字节），已忽略", len(buffer))
            self._drop_socket(socket)
            return
        while b"\n" in buffer:
            raw, _, rest = bytes(buffer).partition(b"\n")
            buffer[:] = rest
            self._dispatch(raw)

    @Slot()
    def _on_disconnected(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket):
            self._drop_socket(socket)

    def _dispatch(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _logger.warning("单实例消息不是合法 JSON，已忽略")
            return
        if not isinstance(data, dict):
            return
        raw_paths = data.get("paths")
        paths = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []
        raw_cwd = data.get("cwd")
        cwd = str(raw_cwd) if isinstance(raw_cwd, str) else str(Path.cwd())
        self.messageReceived.emit(paths, cwd)

    def _drop_socket(self, socket: QLocalSocket) -> None:
        if self._buffers.pop(socket, None) is None:
            return
        socket.abort()
        socket.deleteLater()
