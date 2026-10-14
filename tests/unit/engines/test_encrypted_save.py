"""加密文档的保存语义测试。

覆盖三类回归：

1. **整份保存不得把加密文档写成明文**（另存为 / 合并后的内存文档 / 后台线程快照都走这条路）。
2. **权限位必须原样保留**——``save(permissions=...)`` 省略时会写成 ``-4``（完全放开），
   而 ``doc.permissions`` 在用所有者密码打开时也返回 ``-4``，两者都会悄悄放权。
3. 增量保存（``PDF_ENCRYPT_KEEP``）同样不得丢加密。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.types import Point

USER_PW = "userpw"
OWNER_PW = "ownerpw"
# 只允许打印的 /P：PDF 要求的保留位（bit7-8 与 bit13-32）已置位。
PRINT_ONLY_P = -3900


@pytest.fixture
def engine() -> PyMuPdfEngine:
    return PyMuPdfEngine()


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    """AES-256 加密、仅允许打印的 PDF。"""
    import pymupdf

    path = tmp_path / "restricted.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello")
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=USER_PW,
        owner_pw=OWNER_PW,
        permissions=int(pymupdf.PDF_PERM_PRINT),
    )
    doc.close()
    assert _raw_p(path) == PRINT_ONLY_P, "样本构造失败：权限位不是预期的仅打印"
    return path


def _encrypt_value(path: Path) -> tuple[str, str]:
    """文件 trailer 里 Encryption 字典的 ``(类型, 文本)``；没有该键时类型为 null。"""
    import pymupdf

    doc = pymupdf.open(path)
    kind, value = doc.xref_get_key(-1, "Encrypt")
    doc.close()
    return kind, value


def _raw_p(path: Path) -> int:
    """直接读文件里 Encryption 字典的 ``/P``（与认证级别无关，是客观事实来源）。"""
    kind, value = _encrypt_value(path)
    assert kind == "dict", f"/Encrypt 不是直接字典：{kind}"
    match = re.search(r"/P\s+(-?\d+)", value)
    assert match, f"未找到 /P：{value}"
    return int(match.group(1))


class TestEncryptedSave:
    def test_open_records_password(self, engine: PyMuPdfEngine, encrypted_pdf: Path) -> None:
        doc = engine.open(encrypted_pdf, password=USER_PW)
        assert doc.password == USER_PW
        assert doc.auth_level == 2
        engine.close(doc)

    def test_whole_save_keeps_encryption(
        self, engine: PyMuPdfEngine, encrypted_pdf: Path, tmp_path: Path
    ) -> None:
        """另存为必须仍是加密文档——否则用户以为文件还受保护。"""
        import pymupdf

        doc = engine.open(encrypted_pdf, password=USER_PW)
        engine.save(doc, tmp_path / "out.pdf", incremental=False)
        engine.close(doc)

        reopened = pymupdf.open(tmp_path / "out.pdf")
        assert reopened.needs_pass, "整份保存把加密文档写成了明文"
        assert reopened.authenticate(USER_PW) == 2
        text = reopened[0].get_text()
        reopened.close()
        assert "hello" in text

    def test_whole_save_keeps_permissions(
        self, engine: PyMuPdfEngine, encrypted_pdf: Path, tmp_path: Path
    ) -> None:
        """权限位必须与源文件一致；省略 permissions 会被写成 -4（完全放开）。"""
        doc = engine.open(encrypted_pdf, password=USER_PW)
        assert doc.permissions == PRINT_ONLY_P
        engine.save(doc, tmp_path / "out.pdf", incremental=False)
        engine.close(doc)
        assert _raw_p(tmp_path / "out.pdf") == PRINT_ONLY_P

    def test_owner_password_does_not_escalate_permissions(
        self, engine: PyMuPdfEngine, encrypted_pdf: Path, tmp_path: Path
    ) -> None:
        """用所有者密码打开时，权限位仍取文件里的 /P，而不是 MuPDF 报的 -4。"""
        doc = engine.open(encrypted_pdf, password=OWNER_PW)
        assert doc.auth_level == 4
        assert doc.permissions == PRINT_ONLY_P, "所有者密码下的权限位被报成 -4（会放权）"
        engine.save(doc, tmp_path / "out.pdf", incremental=False)
        engine.close(doc)
        assert _raw_p(tmp_path / "out.pdf") == PRINT_ONLY_P

    def test_incremental_save_keeps_encryption(
        self, engine: PyMuPdfEngine, encrypted_pdf: Path
    ) -> None:
        import pymupdf

        doc = engine.open(encrypted_pdf, password=USER_PW)
        engine.rotate_page(doc, 0, 90)
        engine.save(doc, encrypted_pdf, incremental=True)
        engine.close(doc)

        reopened = pymupdf.open(encrypted_pdf)
        assert reopened.needs_pass, "增量保存把加密文档写成了明文"
        assert reopened.authenticate(USER_PW) == 2
        assert reopened[0].rotation == 90
        reopened.close()

    def test_owner_only_encryption_stays_encrypted(
        self, engine: PyMuPdfEngine, tmp_path: Path
    ) -> None:
        """只设所有者密码（用户密码为空）的文件不需要输密码，但保存时也不能变明文。"""
        import pymupdf

        src = tmp_path / "owner_only.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "hello")
        doc.save(
            str(src),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="",
            owner_pw=OWNER_PW,
            permissions=int(pymupdf.PDF_PERM_PRINT),
        )
        doc.close()

        handle = engine.open(src)  # 无需密码
        assert handle.password is None
        assert handle.is_encrypted is True
        engine.save(handle, tmp_path / "out.pdf", incremental=False)
        engine.close(handle)

        kind, value = _encrypt_value(tmp_path / "out.pdf")
        assert kind == "dict", "整份保存把「只要所有者密码」的文件写成了明文"
        assert f"/P {PRINT_ONLY_P}" in value

    def test_merge_keeps_encryption_and_permissions(
        self, engine: PyMuPdfEngine, encrypted_pdf: Path, tmp_path: Path
    ) -> None:
        """合并会把文档从内存重开（丢 journal 与 /Encrypt），保存时必须重新加密且不放权。"""
        import pymupdf

        other = tmp_path / "other.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "other")
        doc.save(str(other))
        doc.close()

        handle = engine.open(encrypted_pdf, password=USER_PW)
        assert engine.merge(handle, other) == 1
        engine.save(handle, tmp_path / "merged.pdf", incremental=False)
        engine.close(handle)

        out = tmp_path / "merged.pdf"
        assert _raw_p(out) == PRINT_ONLY_P
        reopened = pymupdf.open(out)
        assert reopened.needs_pass, "合并后的整份保存把加密文档写成了明文"
        assert reopened.authenticate(USER_PW)
        text = "".join(reopened[i].get_text() for i in range(reopened.page_count))
        reopened.close()
        assert "hello" in text
        assert "other" in text

    def test_metadata_reports_encrypted(self, engine: PyMuPdfEngine, encrypted_pdf: Path) -> None:
        """已认证的加密文档也要报 is_encrypted=True（PyMuPDF 原生属性此时是 False）。"""
        doc = engine.open(encrypted_pdf, password=USER_PW)
        assert engine.metadata(doc).is_encrypted is True
        engine.close(doc)

    def test_plain_document_stays_plain(self, engine: PyMuPdfEngine, tmp_path: Path) -> None:
        """未加密文档的整份保存行为不变（不引入密码）。"""
        import pymupdf

        src = tmp_path / "plain.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "plain")
        doc.save(str(src))
        doc.close()

        handle = engine.open(src)
        assert handle.password is None
        assert handle.permissions is None
        engine.insert_text(handle, 0, Point(60, 100), "中文")
        engine.save(handle, tmp_path / "plain_out.pdf", incremental=False)
        engine.close(handle)

        reopened = pymupdf.open(tmp_path / "plain_out.pdf")
        assert not reopened.needs_pass
        text = reopened[0].get_text()
        reopened.close()
        assert "中文" in text
