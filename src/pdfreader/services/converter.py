"""格式转换服务层。

实现 PDF 与其他格式的互转。纯 Python 实现,通过引擎层访问 PDF。
转换是耗时操作,调用方应放在后台线程(见 workers/convert_worker.py)。

支持的转换:
- PDF → 图片(PNG/JPEG): PyMuPDF get_pixmap
- 图片 → PDF: PyMuPDF insert_image
- PDF → TXT: PyMuPDF get_text
- PDF → Word(docx): pdf2docx
- PDF → Excel(xlsx): pdfplumber + openpyxl(提取表格)

注:Word → PDF 需 LibreOffice,本阶段暂不支持(检测到时提示安装)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ConvertError", "ConvertFormat", "ConvertResult", "ConvertService"]


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
    """转换结果。"""

    output_path: Path
    format_name: str
    page_count: int = 0


class ConvertService:
    """格式转换服务。

    所有方法接收源 PDF 路径与输出路径,执行转换。
    Word→PDF 暂不支持(需 LibreOffice)。
    """

    def convert(
        self,
        source_pdf: Path,
        output_path: Path,
        target_format: str,
        dpi: int = 150,
    ) -> ConvertResult:
        """转换 PDF 到目标格式。

        Args:
            source_pdf: 源 PDF 路径
            output_path: 输出路径(不含扩展名,会自动补)
            target_format: 目标格式键(FORMATS 的 key)
            dpi: 图片类转换的 DPI
        """
        fmt = FORMATS.get(target_format)
        if fmt is None:
            raise ConvertError(f"不支持的格式:{target_format}")

        # 自动补扩展名
        if output_path.suffix.lower() != fmt.extension:
            output_path = output_path.with_suffix(fmt.extension)

        if target_format in ("png", "jpeg"):
            return self._to_image(source_pdf, output_path, target_format, dpi)
        elif target_format == "txt":
            return self._to_text(source_pdf, output_path)
        elif target_format == "docx":
            return self._to_docx(source_pdf, output_path)
        elif target_format == "xlsx":
            return self._to_xlsx(source_pdf, output_path)
        else:
            raise ConvertError(f"格式 {target_format} 暂不支持从 PDF 转换")

    def image_to_pdf(self, image_paths: list[Path], output_path: Path) -> ConvertResult:
        """图片 → PDF。"""
        import pymupdf

        doc = pymupdf.open()
        for img_path in image_paths:
            page = doc.new_page()
            page.insert_image(page.rect, filename=str(img_path))
        doc.save(str(output_path), garbage=4, deflate=True)
        count = doc.page_count
        doc.close()
        return ConvertResult(output_path, "PDF", count)

    # ------------------------------------------------------------------
    # 各格式实现
    # ------------------------------------------------------------------
    def _to_image(self, source: Path, output: Path, fmt: str, dpi: int) -> ConvertResult:
        import pymupdf

        doc = pymupdf.open(source)
        count = doc.page_count
        # 多页时输出到目录(每页一个文件),单页直接输出
        if count == 1:
            pix = doc[0].get_pixmap(dpi=dpi)
            pix.save(str(output))
        else:
            # 多页:output 作为前缀,生成 output_1.png 等
            stem = output.stem
            parent = output.parent
            for i in range(count):
                pix = doc[i].get_pixmap(dpi=dpi)
                page_path = parent / f"{stem}_{i + 1}{output.suffix}"
                pix.save(str(page_path))
        doc.close()
        return ConvertResult(output, FORMATS[fmt].name, count)

    def _to_text(self, source: Path, output: Path) -> ConvertResult:
        import pymupdf

        doc = pymupdf.open(source)
        text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
        count = doc.page_count
        doc.close()
        output.write_text(text, encoding="utf-8")
        return ConvertResult(output, "纯文本", count)

    def _to_docx(self, source: Path, output: Path) -> ConvertResult:
        try:
            from pdf2docx import Converter
        except ImportError as exc:
            raise ConvertError("PDF→Word 需要 pdf2docx,未安装") from exc
        try:
            cv = Converter(str(source))
            cv.convert(str(output))
            cv.close()
        except Exception as exc:
            raise ConvertError(f"PDF→Word 失败:{exc}") from exc
        return ConvertResult(output, "Word 文档", 0)

    def _to_xlsx(self, source: Path, output: Path) -> ConvertResult:
        try:
            import pdfplumber
            from openpyxl import Workbook
        except ImportError as exc:
            raise ConvertError("PDF→Excel 需要 pdfplumber/openpyxl,未安装") from exc
        try:
            wb = Workbook()
            ws = wb.active
            row_count = 0
            with pdfplumber.open(str(source)) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables() or []:
                        for row in table:
                            ws.append([c or "" for c in row])
                            row_count += 1
            wb.save(str(output))
        except Exception as exc:
            raise ConvertError(f"PDF→Excel 失败:{exc}") from exc
        return ConvertResult(output, "Excel 表格", row_count)
