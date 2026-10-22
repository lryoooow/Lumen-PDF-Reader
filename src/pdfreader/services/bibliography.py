"""书目交换、DOI 查询和常用期刊论文引用模板；不操作 Zotero 数据库。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import bibtexparser
import httpx
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser

_DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"]+", re.I)


@dataclass(frozen=True)
class Reference:
    key: str
    title: str
    authors: tuple[str, ...] = ()  # 姓, 名；团体作者可用单个完整字符串
    year: str = ""
    doi: str = ""
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    kind: str = "article"
    extra: dict[str, str] = field(default_factory=dict)


def extract_doi(text: str) -> str:
    match = _DOI.search(text)
    if match is None:
        return ""
    doi = match.group().rstrip(".,;，。；")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    return doi


def export_bibtex(references: Sequence[Reference]) -> str:
    database = BibDatabase()
    database.entries = [
        {
            **ref.extra,
            "ENTRYTYPE": ref.kind,
            "ID": ref.key,
            **{
                key: value
                for key, value in {
                    "title": ref.title,
                    "author": " and ".join(ref.authors),
                    "year": ref.year,
                    "doi": ref.doi,
                    "journal": ref.journal,
                    "volume": ref.volume,
                    "number": ref.issue,
                    "pages": ref.pages,
                }.items()
                if value
            },
        }
        for ref in references
    ]
    result: str = bibtexparser.dumps(database)
    return result


def import_bibtex(text: str) -> list[Reference]:
    if len(text) > 4_000_000:
        raise ValueError("书目文件过大")
    database = bibtexparser.loads(text, parser=BibTexParser(ignore_nonstandard_types=False))
    entries: list[dict[str, str]] = database.entries
    if not entries:
        raise ValueError("未找到可读取的 BibTeX 条目")
    return [
        Reference(
            entry.get("ID", "item"),
            entry.get("title", ""),
            tuple(a.strip() for a in entry.get("author", "").split(" and ") if a.strip()),
            entry.get("year", ""),
            entry.get("doi", ""),
            entry.get("journal", ""),
            entry.get("volume", ""),
            entry.get("number", ""),
            entry.get("pages", ""),
            entry.get("ENTRYTYPE", "article"),
            {
                key: value
                for key, value in entry.items()
                if key
                not in {
                    "ID",
                    "ENTRYTYPE",
                    "title",
                    "author",
                    "year",
                    "doi",
                    "journal",
                    "volume",
                    "number",
                    "pages",
                }
            },
        )
        for entry in entries
    ]


def export_ris(references: Sequence[Reference]) -> str:
    lines: list[str] = []
    for ref in references:
        values = [
            (
                "TY",
                {
                    "article": "JOUR",
                    "book": "BOOK",
                    "inproceedings": "CONF",
                    "phdthesis": "THES",
                }.get(ref.kind, "GEN"),
            ),
            ("ID", ref.key),
            ("TI", ref.title),
            *(("AU", author) for author in ref.authors),
            ("PY", ref.year),
            ("DO", ref.doi),
            ("JO", ref.journal),
            ("VL", ref.volume),
            ("IS", ref.issue),
            ("SP", ref.pages),
        ]
        for tag, value in values:
            if value:
                lines.append(f"{tag}  - {value.replace(chr(10), ' ').replace(chr(13), ' ')}")
        lines.extend(["ER  - ", ""])
    return "\n".join(lines)


def import_ris(text: str) -> list[Reference]:
    if len(text) > 4_000_000:
        raise ValueError("书目文件过大")
    records: list[dict[str, list[str]]] = []
    record: dict[str, list[str]] | None = None
    previous = ""
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s{2}-\s?(.*)$", line)
        if match:
            tag, value = match.groups()
            if tag == "TY":
                if record is not None:
                    raise ValueError("RIS 条目缺少结束标记 ER")
                record = {}
            if record is not None:
                record.setdefault(tag, []).append(value)
                previous = tag
                if tag == "ER":
                    records.append(record)
                    record = None
        elif record is not None and previous and line.strip():
            record[previous][-1] += " " + line.strip()
    if record is not None or not records:
        raise ValueError("RIS 文件缺少有效完整条目")

    def first(item: dict[str, list[str]], *keys: str) -> str:
        return next((item[key][0] for key in keys if item.get(key)), "")

    return [
        Reference(
            first(item, "ID") or f"item{i + 1}",
            first(item, "TI", "T1"),
            tuple(item.get("AU", item.get("A1", []))),
            first(item, "PY", "Y1")[:4],
            first(item, "DO"),
            first(item, "JO", "JF", "T2"),
            first(item, "VL"),
            first(item, "IS"),
            first(item, "SP") + ("-" + first(item, "EP") if first(item, "EP") else ""),
            kind={
                "JOUR": "article",
                "BOOK": "book",
                "CONF": "inproceedings",
                "THES": "phdthesis",
            }.get(first(item, "TY"), "misc"),
        )
        for i, item in enumerate(records)
    ]


def export_csl_json(references: Sequence[Reference]) -> str:
    return json.dumps(
        [
            {
                "id": ref.key,
                "type": {
                    "article": "article-journal",
                    "book": "book",
                    "inproceedings": "paper-conference",
                    "phdthesis": "thesis",
                }.get(ref.kind, "document"),
                "title": ref.title,
                "author": [
                    {
                        "family": author.split(",", 1)[0].strip(),
                        "given": author.split(",", 1)[1].strip(),
                    }
                    if "," in author
                    else {"literal": author}
                    for author in ref.authors
                ],
                "issued": {"date-parts": [[int(ref.year)]]}
                if ref.year.isdigit()
                else {"literal": ref.year},
                "DOI": ref.doi,
                "container-title": ref.journal,
                "volume": ref.volume,
                "issue": ref.issue,
                "page": ref.pages,
            }
            for ref in references
        ],
        ensure_ascii=False,
        indent=2,
    )


def format_citation(reference: Reference, style: str, page: int | None = None) -> str:
    """常用期刊论文模板；缺失字段不编造，其他文献类型建议交由 CSL 工具排版。"""
    if style == "BibTeX":
        return export_bibtex([reference])
    author = ", ".join(reference.authors) or "佚名"
    if style == "GB/T 7714-2015":
        if len(reference.authors) > 3:
            author = ", ".join(reference.authors[:3]) + ", et al"
        marker = {"article": "J", "book": "M", "inproceedings": "C", "phdthesis": "D"}.get(
            reference.kind, "Z"
        )
        parts = [f"{author}. {reference.title}[{marker}]", reference.year]
        if reference.volume:
            parts[-1] += (
                ", " + reference.volume + (f"({reference.issue})" if reference.issue else "")
            )
        if reference.pages:
            parts[-1] += ": " + reference.pages
        result = (
            parts[0]
            + ". "
            + (reference.journal + ", " if reference.journal else "")
            + parts[1]
            + "."
        )
        if reference.doi:
            result += " DOI: " + reference.doi + "."
    elif style == "APA 7":
        names: list[str] = []
        for name in reference.authors:
            if "," in name:
                family, given = name.split(",", 1)
                names.append(
                    family.strip() + ", " + " ".join(p[0].upper() + "." for p in given.split() if p)
                )
            else:
                names.append(name)
        authors = (
            (", ".join(names[:-1]) + ", & " + names[-1])
            if len(names) > 1
            else (names[0] if names else "佚名")
        )
        result = f"{authors} ({reference.year or 'n.d.'}). {reference.title}."
        if reference.journal:
            result += " " + reference.journal
            if reference.volume:
                result += (
                    ", " + reference.volume + (f"({reference.issue})" if reference.issue else "")
                )
            if reference.pages:
                result += ", " + reference.pages
            result += "."
        if reference.doi:
            result += " https://doi.org/" + reference.doi
    else:
        raise ValueError("不支持的引用模板")
    return result + (f"（引文页码：{page + 1}）" if page is not None else "")


class CrossrefClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def lookup(self, doi: str) -> Reference:
        normalized = extract_doi(doi)
        if not normalized:
            raise ValueError("DOI 无效")
        with httpx.Client(
            transport=self._transport,
            timeout=10,
            follow_redirects=False,
            headers={"User-Agent": "Lumen-PDF-Reader (metadata lookup)"},
        ) as client:
            response = client.get("https://api.crossref.org/works/" + quote(normalized, safe=""))
            if response.status_code != 200:
                raise ValueError(f"Crossref 查询失败（HTTP {response.status_code}）")
            if len(response.content) > 2_000_000:
                raise ValueError("元数据响应过大")
            payload: Any = response.json()
            data: Any = payload.get("message") if isinstance(payload, dict) else None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("title"), list)
            or not data["title"]
        ):
            raise ValueError("Crossref 返回的元数据不完整")
        authors = tuple(
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in data.get("author", [])
            if isinstance(a, dict)
        )
        date = data.get("published", data.get("issued", {}))
        dates = date.get("date-parts", [[]]) if isinstance(date, dict) else [[]]
        year = str(dates[0][0]) if dates and dates[0] else ""
        return Reference(
            normalized,
            str(data["title"][0]),
            authors,
            year,
            normalized,
            str((data.get("container-title") or [""])[0]),
            str(data.get("volume", "")),
            str(data.get("issue", "")),
            str(data.get("page", "")),
        )
