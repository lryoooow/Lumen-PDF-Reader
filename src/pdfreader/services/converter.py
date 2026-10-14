"""格式转换服务层。

实现 PDF 与其他格式的互转。**不直接 import pymupdf**,PDF 读写一律经
pdfreader.engines 的引擎接口(pdf2docx / pdfplumber 等第三方库除外)。
转换是耗时操作（大文档的 docx/xlsx 可达分钟级），**必须**放在后台线程：
GUI 侧统一经 ``workers/convert_worker.py`` 的 ``ConvertWorker`` 调用，主线程只收发结果。

支持的转换:
- PDF → 图片(PNG/JPEG): 引擎 render_page
- 图片 → PDF: 引擎 images_to_pdf
- PDF → TXT: 引擎 extract_text
- PDF → Word(docx): pdf2docx
- PDF → Excel(xlsx): pdfplumber + openpyxl(提取表格)

注:Word → PDF 需 LibreOffice,本阶段暂不支持(检测到时提示安装)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import PdfEngineError

__all__ = ["ConvertError", "ConvertFormat", "ConvertResult", "ConvertService", "image_targets"]


class ConvertError(Exception):
    """转换错误。"""


@dataclass(frozen=True, slots=True)
class ConvertFormat:
    """目标格式描述。"""

    name: str  # 显示名
    extension: str  # 文件扩展名(含点,如 .docx)
    filter: str  # 文件对话框过滤器


# 支持的转换格式
FORMATS = {
    "png": ConvertFormat("PNG 图片", ".png", "PNG 图片 (*.png)"),
    "jpeg": ConvertFormat("JPEG 图片", ".jpg", "JPEG 图片 (*.jpg)"),
    "txt": ConvertFormat("纯文本", ".txt", "文本文件 (*.txt)"),
    "docx": ConvertFormat("Word 文档", ".docx", "Word 文档 (*.docx)"),
    "xlsx": ConvertFormat("Excel 表格", ".xlsx", "Excel 表格 (*.xlsx)"),
    "pdf": ConvertFormat("PDF", ".pdf", "PDF 文件 (*.pdf)"),  # 图片→PDF
}


@dataclass(frozen=True, slots=True)
class ConvertResult:
    """转换结果。

    Attributes:
        output_path: 用户指定的输出路径（已补扩展名）
        format_name: 目标格式显示名
        page_count: 处理的页数（xlsx 为导出的表格行数）
        files: 实际写出的全部文件（多页图片是 ``stem_1.png`` 等，不是 output_path 本身）
    """

    output_path: Path
    format_name: str
    page_count: int = 0
    files: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """转换成功但没有实际内容（例如 xlsx 未检测到任何表格）。"""
        return self.format_name == FORMATS["xlsx"].name and self.page_count == 0


# openpyxl 拒绝写入的控制字符（PDF 表格里偶尔会带出来，整份导出因此失败）
_ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def image_targets(output_path: Path, page_count: int) -> list[Path]:
    """PDF→图片时实际会写出的文件：单页就是 output_path，多页为 ``stem_1.ext`` …"""
    if page_count <= 1:
        return [output_path]
    return [
        output_path.parent / f"{output_path.stem}_{i + 1}{output_path.suffix}"
        for i in range(page_count)
    ]


class ConvertService:
    """格式转换服务。

    所有方法接收源 PDF 路径与输出路径,执行转换。
    Word→PDF 暂不支持(需 LibreOffice)。
    """

    @staticmethod
    def resolve_output(output_path: Path, target_format: str) -> Path:
        """补齐扩展名后的输出路径（与 :meth:`convert` 的实际行为一致）。"""
        fmt = FORMATS.get(target_format)
        if fmt is None:
            raise ConvertError(f"不支持的格式:{target_format}")
        if output_path.suffix.lower() != fmt.extension:
            return output_path.with_suffix(fmt.extension)
        return output_path

    def convert(
        self,
        source_pdf: Path,
        output_path: Path,
        target_format: str,
        dpi: int = 150,
        *,
        password: str | None = None,
    ) -> ConvertResult:
        """转换 PDF 到目标格式。

        Args:
            source_pdf: 源 PDF 路径
            output_path: 输出路径(不含扩展名,会自动补)
            target_format: 目标格式键(FORMATS 的 key)
            dpi: 图片类转换的 DPI
            password: 加密 PDF 的密码（未加密时忽略）
        """
        output_path = self.resolve_output(output_path, target_format)
        try:
            if target_format in ("png", "jpeg"):
                return self._to_image(source_pdf, output_path, target_format, dpi, password)
            if target_format == "txt":
                return self._to_text(source_pdf, output_path, password)
            if target_format == "docx":
                return self._to_docx(source_pdf, output_path, password)
            if target_format == "xlsx":
                return self._to_xlsx(source_pdf, output_path, password)
        except ConvertError:
            raise
        except PdfEngineError as exc:
            raise ConvertError(str(exc)) from exc
        except OSError as exc:
            raise ConvertError(f"写入 {output_path.name} 失败：{exc.strerror or exc}") from exc
        raise ConvertError(f"格式 {target_format} 暂不支持从 PDF 转换")

    def image_to_pdf(self, image_paths: list[Path], output_path: Path) -> ConvertResult:
        """图片 → PDF。"""
        count = PyMuPdfEngine().images_to_pdf(image_paths, output_path)
        return ConvertResult(output_path, "PDF", count)

    # ------------------------------------------------------------------
    # 各格式实现
    # ------------------------------------------------------------------
    def _to_image(
        self, source: Path, output: Path, fmt: str, dpi: int, password: str | None
    ) -> ConvertResult:
        engine = PyMuPdfEngine()
        doc = engine.open(source, password=password)
        try:
            count = engine.page_count(doc)
            zoom = dpi / 72.0
            targets = image_targets(output, count)
            for i, target in enumerate(targets):
                target.write_bytes(engine.pixmap_to_png(engine.render_page(doc, i, zoom)))
        finally:
            engine.close(doc)
        return ConvertResult(output, FORMATS[fmt].name, count, tuple(targets))

    def _to_text(self, source: Path, output: Path, password: str | None) -> ConvertResult:
        engine = PyMuPdfEngine()
        doc = engine.open(source, password=password)
        try:
            count = engine.page_count(doc)
            text = "\n".join(engine.extract_text(doc, i).full_text for i in range(count))
        finally:
            engine.close(doc)
        output.write_text(text, encoding="utf-8")
        return ConvertResult(output, "纯文本", count, (output,))

    def _to_docx(self, source: Path, output: Path, password: str | None) -> ConvertResult:
        try:
            from pdf2docx import Converter
        except ImportError as exc:
            raise ConvertError("PDF→Word 需要 pdf2docx,未安装") from exc
        try:
            cv = Converter(str(source), password=password)
        except Exception as exc:
            raise ConvertError(f"PDF→Word 失败:{exc}") from exc
        try:
            cv.convert(str(output))
        except Exception as exc:
            raise ConvertError(f"PDF→Word 失败:{exc}") from exc
        finally:
            cv.close()  # 异常路径也要关，否则 Windows 上源文件被锁
        return ConvertResult(output, "Word 文档", 0, (output,))

    def _to_xlsx(self, source: Path, output: Path, password: str | None) -> ConvertResult:
        """提取全部表格到一个工作表：每张表前加一行「第 N 页 · 表 M」标题，表间空一行。"""
        try:
            import pdfplumber
            from openpyxl import Workbook
        except ImportError as exc:
            raise ConvertError("PDF→Excel 需要 pdfplumber/openpyxl,未安装") from exc
        try:
            wb = Workbook()
            ws = wb.active
            row_count = 0
            with pdfplumber.open(str(source), password=password or "") as pdf:
                for page_no, page in enumerate(pdf.pages, start=1):
                    for table_no, table in enumerate(page.extract_tables() or [], start=1):
                        if row_count:
                            ws.append([])
                        ws.append([f"第 {page_no} 页 · 表 {table_no}"])
                        for row in table:
                            ws.append([_ILLEGAL_XLSX.sub("", c or "") for c in row])
                            row_count += 1
            wb.save(str(output))
        except Exception as exc:
            raise ConvertError(f"PDF→Excel 失败:{exc}") from exc
        return ConvertResult(output, "Excel 表格", row_count, (output,))
