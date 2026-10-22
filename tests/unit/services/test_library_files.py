from pathlib import Path

from pdfreader.services.library_files import discover_documents, duplicate_files


def test_directory_discovery_and_binary_duplicates_do_not_modify_files(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.PDF"
    third = tmp_path / "c.epub"
    for path, data in ((first, b"same"), (second, b"same"), (third, b"else")):
        path.write_bytes(data)
    (tmp_path / "ignore.txt").write_text("ignore")
    assert len(discover_documents(tmp_path)) == 3
    result = duplicate_files([first, second, third], lambda: False)
    assert result.groups == ((first, second),) and not result.failed
    assert second.read_bytes() == b"same"
    assert duplicate_files([first, second], lambda: True).cancelled
