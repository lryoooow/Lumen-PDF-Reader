from pathlib import Path

import pymupdf

from pdfreader.controllers import DocumentController
from pdfreader.gui.dialogs.reading_workspace import ReadingWorkspace


def test_split_positions_document_switch_and_rotation_are_read_only(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    controllers: list[DocumentController] = []
    for index in range(2):
        path = tmp_path / f"{index}.pdf"
        with pymupdf.open() as doc:
            for page in range(4):
                doc.new_page().insert_text((72, 100), f"document {index} page {page}")
            doc.save(path)
        controller = DocumentController()
        assert controller.open_file(path)
        controllers.append(controller)
    workspace = ReadingWorkspace([(str(i), c) for i, c in enumerate(controllers)])
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.left.page.setValue(3)
    assert workspace.left.view.current_page_num == 2
    assert workspace.right.page.value() == 1
    workspace.right.document.setCurrentIndex(1)
    workspace.right.rotate_preview()
    assert workspace.right.stack.currentIndex() == 1
    assert not controllers[1].is_modified
    assert controllers[1].get_page_bounds()[0].width < controllers[1].get_page_bounds()[0].height
    for _ in range(3):
        workspace.right.rotate_preview()
    assert workspace.right.stack.currentIndex() == 0
    workspace.toggle_presentation()
    assert workspace.isFullScreen() and workspace.right.isHidden()
    workspace.toggle_presentation()
    assert not workspace.isFullScreen() and not workspace.right.isHidden()
    workspace.close()
    workspace.deleteLater()
    for controller in controllers:
        controller.close_document()
