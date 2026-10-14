"""对话框（关于、导出、签名等）。"""

from __future__ import annotations

from pdfreader.gui.dialogs.about_dialog import AboutDialog
from pdfreader.gui.dialogs.export_dialog import ExportDialog
from pdfreader.gui.dialogs.ocr_result_dialog import OcrResultDialog
from pdfreader.gui.dialogs.signature_dialog import SignatureDialog

__all__ = ["AboutDialog", "ExportDialog", "OcrResultDialog", "SignatureDialog"]
