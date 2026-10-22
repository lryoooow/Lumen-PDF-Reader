"""表格文本导出；保留列数、空单元格与 CSV 引号规则。"""

from __future__ import annotations

import csv
import io

from pdfreader.engines.types import TableData


def table_markdown(table: TableData) -> str:
    if not table.rows:
        return ""
    width = max(len(row) for row in table.rows)

    def row_text(row: tuple[str, ...]) -> str:
        values = [
            cell.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>") for cell in row
        ]
        return "| " + " | ".join([*values, *([""] * (width - len(values)))]) + " |"

    return "\n".join(
        [
            row_text(table.rows[0]),
            "| " + " | ".join(["---"] * width) + " |",
            *(row_text(row) for row in table.rows[1:]),
        ]
    )


def table_csv(table: TableData) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerows(table.rows)
    return stream.getvalue()
