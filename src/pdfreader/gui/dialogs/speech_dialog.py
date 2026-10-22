"""用户主动发起的系统本地朗读；无本地引擎时明确提示。"""

from __future__ import annotations

import re
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def speech_segments(text: str) -> list[str]:
    return [
        piece[start : start + 2000]
        for piece in re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+", text)
        if piece.strip()
        for start in range(0, len(piece), 2000)
    ]


class SpeechDialog(QDialog):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("本地朗读")
        self.resize(700, 500)
        self._speech: QTextToSpeech | None = None
        self._queue: deque[str] = deque()
        self._active = False
        self._epoch = 0
        root = QVBoxLayout(self)
        self.text = QPlainTextEdit(text)
        self.text.setAccessibleName("朗读文本")
        root.addWidget(self.text)
        line = QHBoxLayout()
        self.voices = QComboBox()
        self.voices.setAccessibleName("本地语音")
        self.voices.currentIndexChanged.connect(self._voice)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setAccessibleName("朗读速度")
        self.rate.setRange(-10, 10)
        self.rate.valueChanged.connect(self._rate)
        play = QPushButton("开始朗读")
        play.clicked.connect(self.play)
        pause = QPushButton("暂停 / 继续")
        pause.clicked.connect(self.pause)
        stop = QPushButton("停止")
        stop.clicked.connect(self.stop)
        for widget in (self.voices, QLabel("速度"), self.rate, play, pause, stop):
            line.addWidget(widget)
        root.addLayout(line)
        self.status = QLabel("使用系统已安装的本地语音，不发送文本到 AI 服务。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        available = set(QTextToSpeech.availableEngines())
        engine = next(
            (name for name in ("sapi", "winrt", "darwin", "speechd", "flite") if name in available),
            "",
        )
        if not engine:
            self.status.setText("当前系统没有可用的本地朗读引擎，请安装系统语音支持。")
            for button in (play, pause, stop):
                button.setEnabled(False)
            return
        self._speech = QTextToSpeech(engine, self)
        self._speech.stateChanged.connect(self._state)
        self._speech.errorOccurred.connect(self._error)
        for voice in self._speech.availableVoices():
            self.voices.addItem(voice.name(), voice)
        self._voice(self.voices.currentIndex())

    def _voice(self, index: int) -> None:
        if self._speech is not None and index >= 0:
            voices = self._speech.availableVoices()
            if index < len(voices):
                self._speech.setVoice(voices[index])

    def _rate(self, value: int) -> None:
        if self._speech is not None:
            self._speech.setRate(value / 10)

    def play(self) -> None:
        if self._speech is None:
            return
        self.stop()
        text = self.text.toPlainText()
        if len(text) > 100000:
            self.status.setText("一次朗读最多 10 万字符，请缩小选区。")
            return
        self._queue = deque(speech_segments(text))
        self._active = True
        self._next()

    def _next(self) -> None:
        if self._speech is None or not self._active:
            return
        if not self._queue:
            self._active = False
            self.status.setText("朗读结束。")
            return
        self.status.setText(f"正在朗读，剩余 {len(self._queue)} 段。")
        self._epoch += 1
        self._speech.say(self._queue.popleft())

    def _state(self, state: QTextToSpeech.State) -> None:
        if state == QTextToSpeech.State.Ready:
            epoch = self._epoch
            QTimer.singleShot(0, self, lambda epoch=epoch: self._ready(epoch))

    def _ready(self, epoch: int) -> None:
        if (
            self._speech is not None
            and epoch == self._epoch
            and self._speech.state() == QTextToSpeech.State.Ready
        ):
            self._next()

    def _error(self, _reason: QTextToSpeech.ErrorReason, message: str) -> None:
        self.stop()
        self.status.setText("本地朗读失败：" + message)

    def pause(self) -> None:
        if self._speech is not None:
            if self._speech.state() == QTextToSpeech.State.Paused:
                self._speech.resume()
            elif self._speech.state() == QTextToSpeech.State.Speaking:
                self._speech.pause()

    def stop(self) -> None:
        self._epoch += 1
        self._active = False
        self._queue.clear()
        if self._speech is not None:
            self._speech.stop()

    def done(self, result: int) -> None:
        self.stop()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop()
        super().closeEvent(event)
