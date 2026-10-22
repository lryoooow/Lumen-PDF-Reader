from PySide6.QtCore import QObject, Signal
from PySide6.QtTextToSpeech import QTextToSpeech

from pdfreader.gui.dialogs.speech_dialog import SpeechDialog, speech_segments
from pdfreader.gui.theme.manager import ThemeManager
from pdfreader.gui.theme.palette import CONTRAST


def test_local_speech_missing_backend_is_visible(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "pdfreader.gui.dialogs.speech_dialog.QTextToSpeech.availableEngines", lambda: []
    )
    dialog = SpeechDialog("朗读原文")
    qtbot.addWidget(dialog)
    assert "没有可用的本地朗读引擎" in dialog.status.text()
    assert speech_segments("数值 3.14。 Next sentence. More!") == [
        "数值 3.14。",
        "Next sentence.",
        "More!",
    ]
    assert all(len(part) <= 2000 for part in speech_segments("文" * 5000))
    dialog.close()


def test_contrast_mode_uses_readable_palette_without_affecting_system_mode() -> None:
    manager = ThemeManager("contrast")
    assert manager.palette is CONTRAST and manager.is_dark
    assert manager.palette.text == "#FFFFFF" and manager.palette.window == "#000000"
    manager.set_mode("system")
    assert manager.name in ("light", "dark")


def test_speech_queue_pause_stop_without_playing_real_audio(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeSpeech(QObject):
        State = QTextToSpeech.State
        stateChanged = Signal(object)
        errorOccurred = Signal(object, str)

        @staticmethod
        def availableEngines() -> list[str]:
            return ["sapi"]

        def __init__(self, engine, parent):  # type: ignore[no-untyped-def]
            super().__init__(parent)
            self.spoken: list[str] = []
            self.current = self.State.Ready

        def availableVoices(self) -> list:
            return []

        def say(self, text: str) -> None:
            self.spoken.append(text)
            self.current = self.State.Speaking

        def state(self):  # type: ignore[no-untyped-def]
            return self.current

        def pause(self) -> None:
            self.current = self.State.Paused

        def resume(self) -> None:
            self.current = self.State.Speaking

        def stop(self) -> None:
            self.current = self.State.Ready
            self.stateChanged.emit(self.State.Ready)

    monkeypatch.setattr("pdfreader.gui.dialogs.speech_dialog.QTextToSpeech", FakeSpeech)
    dialog = SpeechDialog("第一句。第二句。")
    qtbot.addWidget(dialog)
    dialog.play()
    qtbot.wait(20)
    assert dialog._speech.spoken == ["第一句。"]
    dialog.pause()
    assert dialog._speech.state() == FakeSpeech.State.Paused
    dialog.pause()
    assert dialog._speech.state() == FakeSpeech.State.Speaking
    dialog._speech.current = FakeSpeech.State.Ready
    dialog._speech.stateChanged.emit(FakeSpeech.State.Ready)
    qtbot.waitUntil(lambda: len(dialog._speech.spoken) == 2)
    dialog.stop()
    qtbot.wait(10)
    assert not dialog._active and not dialog._queue
    dialog.close()
