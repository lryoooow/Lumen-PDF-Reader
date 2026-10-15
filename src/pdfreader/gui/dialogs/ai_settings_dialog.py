"""AI 模型设置：管理 OpenAI 兼容服务（Provider）。

左列服务列表（「添加」按厂商预设填好默认地址与常用模型），右侧表单：名称、Base URL、API Key、
模型（可「获取模型列表」）、是否支持图片；「测试连接」用当前模型发一条极短请求，失败原因翻译成中文。

API Key 只在点「确定」后写入系统凭据管理器，``ai.json`` 里没有 Key。
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.services.ai.client import ChatClient
from pdfreader.services.ai.keystore import KeyStore, KeyStoreError
from pdfreader.services.ai.provider import (
    PRESETS,
    AiSettings,
    ProviderConfig,
    new_provider,
    normalize_base_url,
    preset_by_id,
)
from pdfreader.workers.ai_worker import AiWorker

__all__ = ["LANGUAGES", "AiSettingsDialog"]

LANGUAGES = ("简体中文", "繁體中文", "English", "日本語")

ClientFactory = Callable[[str, str], ChatClient]


class AiSettingsDialog(QDialog):
    """AI 模型设置对话框。确定后用 :meth:`result_settings` 取结果（Key 已写入凭据管理器）。"""

    def __init__(
        self,
        settings: AiSettings,
        keystore: KeyStore,
        parent: QWidget | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 模型设置")
        self.setMinimumSize(720, 500)
        self._settings = copy.deepcopy(settings)
        self._keystore = keystore
        self._client_factory: ClientFactory = client_factory or (lambda b, k: ChatClient(b, k))
        self._keys: dict[str, str] = {}  # provider id → 编辑中的 Key（懒加载）
        self._removed: list[str] = []
        self._current: ProviderConfig | None = None
        self._loading = False
        self._worker: AiWorker | None = None
        self._probe_got_reply = False

        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ---- 全局
        top = QFormLayout()
        self.enabled_check = QCheckBox("启用 AI 助读（可选的联网功能，只在你主动发起时发送内容）")
        self.enabled_check.setChecked(self._settings.enabled)
        top.addRow(self.enabled_check)
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.addItems(LANGUAGES)
        self.language_combo.setCurrentText(self._settings.target_language)
        top.addRow("翻译 / 回答语言", self.language_combo)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---- 左：服务列表
        left = QVBoxLayout()
        self.provider_list = QListWidget()
        self.provider_list.setObjectName("AiProviderList")
        self.provider_list.setFixedWidth(200)
        self.provider_list.currentRowChanged.connect(self._on_row_changed)
        left.addWidget(self.provider_list, stretch=1)
        btns = QHBoxLayout()
        self.add_btn = QPushButton("添加")
        add_menu = QMenu(self.add_btn)
        for preset in PRESETS:
            add_menu.addAction(preset.name, lambda p=preset.id: self.add_provider(p))
        self.add_btn.setMenu(add_menu)
        self.remove_btn = QPushButton("删除")
        self.remove_btn.clicked.connect(self.remove_current)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.remove_btn)
        left.addLayout(btns)
        body.addLayout(left)

        # ---- 右：表单
        self.form_host = QWidget()
        form = QFormLayout(self.form_host)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.preset_label = QLabel()
        self.preset_label.setObjectName("AiHint")
        self.preset_label.setWordWrap(True)
        form.addRow("类型", self.preset_label)
        self.name_edit = QLineEdit()
        form.addRow("名称", self.name_edit)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL", self.url_edit)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-…（保存到系统凭据管理器）")
        self.show_key = QCheckBox("显示")
        self.show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.key_edit, stretch=1)
        key_row.addWidget(self.show_key)
        form.addRow("API Key", key_row)
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(220)
        self.fetch_btn = QPushButton("获取模型列表")
        self.fetch_btn.clicked.connect(self.fetch_models)
        model_row.addWidget(self.model_combo, stretch=1)
        model_row.addWidget(self.fetch_btn)
        form.addRow("模型", model_row)
        self.vision_check = QCheckBox("该模型支持图片输入（用于框选公式 / 图表解释）")
        form.addRow("", self.vision_check)
        self.temp_edit = QLineEdit()
        self.temp_edit.setPlaceholderText("留空 = 不发送（推荐，推理模型可能不接受）")
        form.addRow("温度", self.temp_edit)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self.test_connection)
        self.use_btn = QPushButton("设为当前使用")
        self.use_btn.clicked.connect(self._use_current)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.use_btn)
        test_row.addStretch(1)
        form.addRow("", test_row)
        self.status_label = QLabel()
        self.status_label.setObjectName("AiHint")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("", self.status_label)
        body.addWidget(self.form_host, stretch=1)
        root.addLayout(body, stretch=1)

        privacy = QLabel(
            "隐私：内容只会发送到你在这里配置的地址；首次向某个服务发送时会再次确认。"
            "本机模型（如 Ollama）数据不出电脑。厂商地址以其官方文档为准。"
        )
        privacy.setObjectName("AiHint")
        privacy.setWordWrap(True)
        root.addWidget(privacy)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for edit in (self.name_edit, self.url_edit, self.key_edit, self.temp_edit):
            edit.textChanged.connect(self._store_form)
        self.model_combo.currentTextChanged.connect(self._store_form)
        self.vision_check.toggled.connect(self._store_form)

        self._rebuild_list()
        active = self._settings.active()
        if active is not None:
            self.provider_list.setCurrentRow(self._settings.providers.index(active))
        self._update_form_enabled()

    # ------------------------------------------------------------------ 列表
    def _label(self, provider: ProviderConfig) -> str:
        mark = "● " if provider.id == self._settings.active_id else ""
        return f"{mark}{provider.name}"

    def _rebuild_list(self) -> None:
        row = self.provider_list.currentRow()
        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        for provider in self._settings.providers:
            item = QListWidgetItem(self._label(provider))
            item.setToolTip(provider.base_url)
            self.provider_list.addItem(item)
        self.provider_list.blockSignals(False)
        if self._settings.providers:
            self.provider_list.setCurrentRow(min(max(row, 0), len(self._settings.providers) - 1))

    def add_provider(self, preset_id: str) -> ProviderConfig:
        provider = new_provider(preset_id)
        if not self._settings.providers:
            self.enabled_check.setChecked(True)  # 添加第一个服务即表示要用 AI 助读
        self._settings.providers.append(provider)
        if not self._settings.active_id:
            self._settings.active_id = provider.id
        self._keys[provider.id] = ""
        self._rebuild_list()
        self.provider_list.setCurrentRow(len(self._settings.providers) - 1)
        self._update_form_enabled()
        self.name_edit.setFocus()
        return provider

    def remove_current(self) -> None:
        row = self.provider_list.currentRow()
        if not 0 <= row < len(self._settings.providers):
            return
        provider = self._settings.providers.pop(row)
        self._removed.append(provider.id)
        self._keys.pop(provider.id, None)
        if self._settings.active_id == provider.id:
            self._settings.active_id = (
                self._settings.providers[0].id if self._settings.providers else ""
            )
        self._current = None
        self._rebuild_list()
        if not self._settings.providers:
            self._load_form(None)
        self._update_form_enabled()

    def _use_current(self) -> None:
        if self._current is not None:
            self._settings.active_id = self._current.id
            self._rebuild_list()
            self.status_label.setText(f"助读将使用「{self._current.name}」")

    def _update_form_enabled(self) -> None:
        has = self._current is not None
        self.form_host.setEnabled(has)
        self.remove_btn.setEnabled(has)

    def _on_row_changed(self, row: int) -> None:
        self._stop_worker()
        provider = (
            self._settings.providers[row] if 0 <= row < len(self._settings.providers) else None
        )
        self._load_form(provider)
        self._update_form_enabled()

    # ------------------------------------------------------------------ 表单 ⇄ 配置
    def _key_for(self, provider: ProviderConfig) -> str:
        if provider.id not in self._keys:
            try:
                self._keys[provider.id] = self._keystore.get(provider.id)
            except KeyStoreError as exc:
                self._keys[provider.id] = ""
                self.status_label.setText(str(exc))
        return self._keys[provider.id]

    def _load_form(self, provider: ProviderConfig | None) -> None:
        self._loading = True
        self._current = provider
        self.status_label.setText("")
        try:
            if provider is None:
                for edit in (self.name_edit, self.url_edit, self.key_edit, self.temp_edit):
                    edit.clear()
                self.model_combo.clear()
                self.preset_label.setText("")
                self.vision_check.setChecked(False)
                return
            preset = preset_by_id(provider.preset)
            note = f" · {preset.note}" if preset.note else ""
            self.preset_label.setText(f"{preset.name}{note}")
            self.name_edit.setText(provider.name)
            self.url_edit.setText(provider.base_url)
            self.key_edit.setText(self._key_for(provider))
            self.key_edit.setPlaceholderText(
                "本机服务通常不需要 Key"
                if not provider.needs_key
                else "sk-…（保存到系统凭据管理器）"
            )
            self.model_combo.clear()
            self.model_combo.addItems(provider.models)
            self.model_combo.setCurrentText(provider.model)
            self.vision_check.setChecked(provider.vision)
            self.temp_edit.setText(
                "" if provider.temperature is None else str(provider.temperature)
            )
        finally:
            self._loading = False

    def _store_form(self) -> None:
        provider = self._current
        if self._loading or provider is None:
            return
        provider.name = self.name_edit.text().strip() or preset_by_id(provider.preset).name
        provider.base_url = normalize_base_url(self.url_edit.text())
        provider.model = self.model_combo.currentText().strip()
        provider.vision = self.vision_check.isChecked()
        try:
            temperature = float(self.temp_edit.text()) if self.temp_edit.text().strip() else None
        except ValueError:
            temperature = provider.temperature
        provider.temperature = temperature
        self._keys[provider.id] = self.key_edit.text().strip()
        item = self.provider_list.currentItem()
        if item is not None:
            item.setText(self._label(provider))
            item.setToolTip(provider.base_url)

    # ------------------------------------------------------------------ 网络：获取模型 / 测试
    def _client(self) -> ChatClient | None:
        provider = self._current
        if provider is None:
            return None
        self._store_form()
        if not provider.base_url:
            self.status_label.setText("请先填写 Base URL")
            return None
        return self._client_factory(provider.base_url, self._keys.get(provider.id, ""))

    def _start(self, worker: AiWorker, busy_text: str) -> None:
        self._stop_worker()
        self._worker = worker
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_done)
        self.status_label.setText(busy_text)
        self.fetch_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        worker.start()

    def fetch_models(self) -> None:
        client = self._client()
        if client is None:
            return
        worker = AiWorker(self)
        worker.setup_models(client)
        worker.modelsListed.connect(self._on_models)
        self._start(worker, "正在获取模型列表…")

    def test_connection(self) -> None:
        provider = self._current
        client = self._client()
        if client is None or provider is None:
            return
        if not provider.model:
            self.status_label.setText("请先填写或选择模型")
            return
        worker = AiWorker(self)
        worker.setup_chat(
            client,
            [{"role": "user", "content": "Reply with OK."}],
            model=provider.model,
            max_tokens=16,
        )
        self._probe_got_reply = False
        worker.delta.connect(self._on_probe_delta)
        worker.reasoning.connect(self._on_probe_delta)
        worker.finished_text.connect(self._on_probe_done)
        worker.cancelled.connect(self._on_probe_done)
        self._start(worker, f"正在测试 {provider.host} …")

    def _on_probe_delta(self, _text: str) -> None:
        # 收到首个增量即可判定连通，不必等完整回答
        if not self._probe_got_reply and self._worker is not None:
            self._probe_got_reply = True
            self._worker.shutdown()

    def _on_probe_done(self, *_args: object) -> None:
        provider = self._current
        name = provider.model if provider else ""
        self.status_label.setText(f"✓ 连接成功：模型 {name} 可用")

    def _on_models(self, models: list[str]) -> None:
        provider = self._current
        if provider is None:
            return
        if not models:
            self.status_label.setText("服务没有返回模型列表，请手动填写模型名")
            return
        provider.models = list(models)
        current = self.model_combo.currentText()
        self._loading = True
        try:
            self.model_combo.clear()
            self.model_combo.addItems(models)
            self.model_combo.setCurrentText(current or models[0])
        finally:
            self._loading = False
        self._store_form()
        self.status_label.setText(f"✓ 获取到 {len(models)} 个模型")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"✗ {message}")

    def _on_worker_done(self) -> None:
        self.fetch_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        if isinstance(worker, AiWorker):
            worker.deleteLater()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        for signal in (worker.failed, worker.modelsListed, worker.finished_text, worker.cancelled):
            with contextlib.suppress(RuntimeError, TypeError):  # 没有连接时 PySide6 会抛错
                signal.disconnect()
        worker.shutdown()
        worker.wait(3000)
        self._worker = None
        self.fetch_btn.setEnabled(True)
        self.test_btn.setEnabled(True)

    # ------------------------------------------------------------------ 结果
    def result_settings(self) -> AiSettings:
        return self._settings

    def accept(self) -> None:
        self._store_form()
        self._stop_worker()
        self._settings.enabled = self.enabled_check.isChecked()
        self._settings.target_language = self.language_combo.currentText().strip() or "简体中文"
        if self._settings.active() is not None and not self._settings.active_id:
            self._settings.active_id = self._settings.providers[0].id
        try:
            for provider_id, key in self._keys.items():
                self._keystore.set(provider_id, key)
        except KeyStoreError as exc:
            self.status_label.setText(f"✗ {exc}")
            return
        for provider_id in self._removed:
            self._keystore.delete(provider_id)
        # 删除的服务不再保留「已同意外发」记录
        alive = {p.id for p in self._settings.providers}
        self._settings.consented = [c for c in self._settings.consented if c in alive]
        super().accept()

    def reject(self) -> None:
        self._stop_worker()
        super().reject()
