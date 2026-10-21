"""把 ECDICT 的 ecdict.csv 精简成查词卡用的 SQLite 词典。

ECDICT：https://github.com/skywind3000/ECDICT（MIT 协议，作者 Linwei / skywind3000）。
完整 CSV 约 77 万条、66 MB，大半是词组、专名和极少见的词；这里只留单个英文词里的：

* 有词频 / 考试标签 / 柯林斯星级 / 牛津核心词标记的常用词（约 5.8 万）；
* 释义里带学科标注（如 ``[计]`` ``[医]`` ``[化]``）的小写专业词（再约 9.6 万）。

用法::

    uv run python scripts/build_dictionary.py <ecdict.csv>

产物 ``src/pdfreader/resources/dict/ecdict-lite.db``（随安装包发布）与同目录的 LICENSE。
表结构见 :mod:`pdfreader.services.dictionary`。
"""

from __future__ import annotations

import contextlib
import csv
import re
import sqlite3
import sys
from pathlib import Path

# 让 Windows 控制台也能输出中文 / emoji
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src" / "pdfreader" / "resources" / "dict"
OUT = OUT_DIR / "ecdict-lite.db"

_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")
# 学科标注：[计] [医] [化] [经] ……（排除网络释义、人名、地名这类非学科标注）
_DOMAIN = re.compile(r"\[(?!网络|人名|地名|姓氏)[^\]]{1,4}\]")
_MAX_SENSE_LINES = 8  # 每条最多保留的释义行（极少数词有几十行网络释义）
_FORM_KINDS = frozenset("pdi3srt")  # 过去式 / 过去分词 / 现在分词 / 三单 / 复数 / 比较级 / 最高级


def _keep(row: dict[str, str]) -> bool:
    word, translation = row["word"], row["translation"].strip()
    if not _WORD.match(word) or not translation:
        return False
    common = (
        int(row["bnc"] or 0) > 0
        or int(row["frq"] or 0) > 0
        or bool(row["tag"].strip())
        or (row["collins"] or "0") != "0"
        or row["oxford"] == "1"
    )
    technical = word.islower() and len(word) >= 4 and bool(_DOMAIN.search(translation))
    return common or technical


def _senses(translation: str) -> str:
    # CSV 里换行写成字面量 \n
    lines = [ln.strip() for ln in translation.replace("\\n", "\n").splitlines() if ln.strip()]
    return "\n".join(lines[:_MAX_SENSE_LINES])


def build(csv_path: Path, out: Path = OUT) -> int:
    csv.field_size_limit(10**8)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        PRAGMA page_size = 4096;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
        CREATE TABLE words(
            key TEXT NOT NULL,          -- 小写，查询用
            word TEXT NOT NULL,         -- 原样（区分 US / us）
            phonetic TEXT NOT NULL,
            translation TEXT NOT NULL,  -- 中文释义，一行一个词性
            exchange TEXT NOT NULL,     -- 词形变化（ECDICT 格式：p:过去式/d:过去分词/…/0:原形/1:变形类别）
            tag TEXT NOT NULL,          -- 考试标签（cet4 cet6 ky toefl ielts gre …）
            PRIMARY KEY (key, word)
        ) WITHOUT ROWID;
        -- 不规则变形反查原形（went → go、children → child）：只收本身不是词条的变形
        CREATE TABLE forms(
            form TEXT NOT NULL PRIMARY KEY,  -- 小写
            lemma TEXT NOT NULL,
            kind TEXT NOT NULL              -- p / d / i / 3 / s / r / t
        ) WITHOUT ROWID;
        """
    )
    count = 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = (
            (
                r["word"].lower(),
                r["word"],
                r["phonetic"].strip(),
                _senses(r["translation"]),
                r["exchange"].strip(),
                r["tag"].strip(),
            )
            for r in csv.DictReader(f)
            if _keep(r)
        )
        for batch in _batched(rows, 5000):
            db.executemany("INSERT OR IGNORE INTO words VALUES (?, ?, ?, ?, ?, ?)", batch)
            count += len(batch)
    forms: dict[str, tuple[str, str]] = {}
    for word, exchange in db.execute("SELECT word, exchange FROM words WHERE exchange != ''"):
        for part in exchange.split("/"):
            kind, _, form = part.partition(":")
            if kind in _FORM_KINDS and form and _WORD.match(form):
                forms.setdefault(form.lower(), (word, kind))
    existing = {key for (key,) in db.execute("SELECT DISTINCT key FROM words")}
    db.executemany(
        "INSERT INTO forms VALUES (?, ?, ?)",
        [(form, lemma, kind) for form, (lemma, kind) in forms.items() if form not in existing],
    )
    db.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [
            ("source", "ECDICT https://github.com/skywind3000/ECDICT"),
            ("license", "MIT"),
            ("entries", str(count)),
        ],
    )
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    return count


def _batched(rows, size: int):  # type: ignore[no-untyped-def]
    batch: list[tuple[str, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    count = build(Path(sys.argv[1]))
    size = OUT.stat().st_size / 1e6
    print(f"✅ {count} 条 → {OUT}（{size:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
