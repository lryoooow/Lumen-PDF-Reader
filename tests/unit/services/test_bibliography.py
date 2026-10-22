import json

import httpx
import pytest

from pdfreader.services.bibliography import (
    CrossrefClient,
    Reference,
    export_bibtex,
    export_csl_json,
    export_ris,
    extract_doi,
    format_citation,
    import_bibtex,
    import_ris,
)


def test_bibliography_roundtrip_preserves_supported_fields() -> None:
    ref = Reference(
        "smith2024",
        "A {nested} title",
        ("Smith, John", "李四"),
        "2024",
        "10.1000/test",
        "Journal",
        "12",
        "2",
        "10-20",
    )
    assert import_bibtex(export_bibtex([ref])) == [ref]
    assert import_ris(export_ris([ref])) == [ref]
    data = json.loads(export_csl_json([ref]))[0]
    assert data["DOI"] == ref.doi and data["author"][0]["family"] == "Smith"
    assert data["issued"]["date-parts"] == [[2024]]
    with pytest.raises(ValueError):
        import_ris("TY  - JOUR\nTI  - incomplete")
    with pytest.raises(ValueError):
        import_bibtex("invalid")


def test_journal_citation_templates_and_page() -> None:
    ref = Reference(
        "smith2024",
        "A result",
        ("Smith, John",),
        "2024",
        "10.1000/test",
        "Journal",
        "12",
        "2",
        "10-20",
    )
    assert (
        format_citation(ref, "APA 7")
        == "Smith, J. (2024). A result. Journal, 12(2), 10-20. https://doi.org/10.1000/test"
    )
    assert format_citation(ref, "GB/T 7714-2015").startswith(
        "Smith, John. A result[J]. Journal, 2024, 12(2): 10-20."
    )
    assert "引文页码：3" in format_citation(ref, "APA 7", 2)
    assert "@article" in format_citation(ref, "BibTeX")


def test_crossref_only_sends_doi_and_handles_failures() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "title": ["Title"],
                    "author": [{"family": "Smith", "given": "John"}],
                    "published": {"date-parts": [[2024]]},
                }
            },
        )

    client = CrossrefClient(transport=httpx.MockTransport(handler))
    assert client.lookup("https://doi.org/10.1000/test").authors == ("Smith, John",)
    assert requests[0].url.host == "api.crossref.org"
    assert requests[0].content == b""
    assert extract_doi("doi:10.1000/test).") == "10.1000/test"
    with pytest.raises(ValueError, match="无效"):
        client.lookup("no DOI")
    failure = CrossrefClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(ValueError, match="429"):
        failure.lookup("10.1000/test")
