"""转换服务测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfreader.services.converter import (
    FORMATS,
    ConvertError,
    ConvertService,
)

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"


@pytest.fixture
def service() -> ConvertService:
    return ConvertService()


class TestFormats:
    def test_formats_defined(self) -> None:
        for key in ["png", "txt", "docx", "xlsx"]:
            assert key in FORMATS

    def test_format_extension(self) -> None:
        assert FORMATS["docx"].extension == ".docx"
        assert FORMATS["png"].extension == ".png"


class TestConvertImage:
    def test_to_png(self, service: ConvertService, tmp_path: Path) -> None:
        # simple_text 有 3 页,多页时生成 page_1.png 等
        out = tmp_path / "page"
        result = service.convert(SIMPLE, out, "png", dpi=72)
        # 多页应生成 3 个带序号的文件
        pngs = list(tmp_path.glob("page*.png"))
        assert len(pngs) == 3
        assert result.page_count == 3

    def test_to_png_multi_page(self, service: ConvertService, tmp_path: Path) -> None:
        out = tmp_path / "doc"
        result = service.convert(SIMPLE, out, "png", dpi=72)
        # simple_text 有 3 页,应生成 3 个文件
        pngs = list(tmp_path.glob("doc*.png"))
        assert len(pngs) == 3
        assert result.page_count == 3


class TestConvertText:
    def test_to_txt(self, service: ConvertService, tmp_path: Path) -> None:
        out = tmp_path / "out"
        result = service.convert(SIMPLE, out, "txt")
        text = result.output_path.read_text(encoding="utf-8")
        assert "Page" in text
        assert result.page_count == 3


class TestConvertWord:
    def test_to_docx(self, service: ConvertService, tmp_path: Path) -> None:
        out = tmp_path / "out"
        result = service.convert(SIMPLE, out, "docx")
        assert result.output_path.exists()
        assert result.output_path.suffix == ".docx"
        assert result.output_path.stat().st_size > 0


class TestConvertExcel:
    def test_to_xlsx(self, service: ConvertService, tmp_path: Path) -> None:
        out = tmp_path / "out"
        # simple_text 无表格,但应不报错
        result = service.convert(SIMPLE, out, "xlsx")
        assert result.output_path.exists()
        assert result.output_path.suffix == ".xlsx"


class TestImageToPdf:
    def test_image_to_pdf(self, service: ConvertService, tmp_path: Path) -> None:
        # 先生成一张图片
        import pymupdf

        doc = pymupdf.open(SIMPLE)
        img_path = tmp_path / "page.png"
        doc[0].get_pixmap(dpi=100).save(str(img_path))
        doc.close()
        # 再转回 PDF
        out = tmp_path / "from_img.pdf"
        result = service.image_to_pdf([img_path], out)
        assert out.exists()
        assert result.page_count == 1


class TestErrors:
    def test_unsupported_format(self, service: ConvertService, tmp_path: Path) -> None:
        with pytest.raises(ConvertError):
            service.convert(SIMPLE, tmp_path / "x", "bogus")

    def test_auto_extension(self, service: ConvertService, tmp_path: Path) -> None:
        """输出路径无扩展名时自动补。"""
        out = tmp_path / "noext"  # 无扩展名
        result = service.convert(SIMPLE, out, "txt")
        assert result.output_path.suffix == ".txt"


class TestConvertRound3:
    """第三轮：加密文档、实际输出文件列表。"""

    ENCRYPTED = SAMPLES / "encrypted.pdf"

    def test_encrypted_source_with_password(self, service: ConvertService, tmp_path: Path) -> None:
        result = service.convert(self.ENCRYPTED, tmp_path / "enc", "txt", password="secret")
        assert "Encrypted content" in result.output_path.read_text(encoding="utf-8")

    def test_encrypted_source_without_password_fails_clearly(
        self, service: ConvertService, tmp_path: Path
    ) -> None:
        with pytest.raises(ConvertError, match="密码"):
            service.convert(self.ENCRYPTED, tmp_path / "enc", "txt")

    def test_encrypted_to_docx_and_xlsx(self, service: ConvertService, tmp_path: Path) -> None:
        assert service.convert(self.ENCRYPTED, tmp_path / "e", "docx", password="secret").files
        assert service.convert(self.ENCRYPTED, tmp_path / "e", "xlsx", password="secret").files

    def test_multi_page_image_lists_real_files(
        self, service: ConvertService, tmp_path: Path
    ) -> None:
        result = service.convert(SIMPLE, tmp_path / "doc", "png", dpi=36)
        assert [p.name for p in result.files] == ["doc_1.png", "doc_2.png", "doc_3.png"]
        assert all(p.exists() for p in result.files)

    def test_xlsx_without_tables_is_flagged_empty(
        self, service: ConvertService, tmp_path: Path
    ) -> None:
        result = service.convert(SIMPLE, tmp_path / "t", "xlsx")
        assert result.is_empty

    def test_resolve_output(self) -> None:
        assert ConvertService.resolve_output(Path("a/b"), "docx") == Path("a/b.docx")
        assert ConvertService.resolve_output(Path("a/b.DOCX"), "docx") == Path("a/b.DOCX")
