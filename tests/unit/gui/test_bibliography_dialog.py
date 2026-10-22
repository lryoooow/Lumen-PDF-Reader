import httpx

from pdfreader.gui.dialogs.bibliography_dialog import BibliographyDialog
from pdfreader.services.bibliography import CrossrefClient, Reference
from pdfreader.workers.metadata_worker import MetadataWorker


def test_metadata_background_preview_and_edit_protection(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = CrossrefClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"message": {"title": ["DOI title"], "issued": {"date-parts": [[2024]]}}}
            )
        )
    )
    monkeypatch.setattr(
        "pdfreader.gui.dialogs.bibliography_dialog.MetadataWorker",
        lambda doi: MetadataWorker(doi, client),
    )
    dialog = BibliographyDialog(Reference("paper", "Original", doi="10.1000/test"))
    qtbot.addWidget(dialog)
    dialog.lookup()
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
    assert dialog.fields["title"].text() == "DOI title"
    dialog.styles.setCurrentText("APA 7")
    dialog.page.setValue(3)
    assert "引文页码：3" in dialog.citation()
    dialog.lookup()
    dialog.fields["title"].setText("User edit")
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
    assert dialog.fields["title"].text() == "User edit"
    assert "未覆盖" in dialog.status.text()
    dialog.close()
