"""Минимальный писатель .xlsx (OOXML) без внешних зависимостей.

Книге выгрузки нужны только заголовок, строки, три формата ячеек (текст, дата-
время, число) и ширины колонок — ради этого не стоит тянуть openpyxl: сборку
.exe приходится держать на закреплённых версиях (requirements-windows.txt), а
каждый новый пакет в onefile — это и вес, и риск, что PyInstaller недоберёт его
динамические импорты. Здесь весь формат на виду и проверяется тестами.

Ограничения намеренные: один лист, строки пишутся как inline-строки (без
sharedStrings), формул нет.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

# Excel считает дни от 1899-12-30 (эпоха 1900 с исторической ошибкой
# високосного 1900 года, которую задаёт сам формат).
EXCEL_EPOCH = datetime(1899, 12, 30)

# Номера стилей в styles.xml ниже: порядок cellXfs менять только вместе с ними.
STYLE_TEXT = 0
STYLE_HEADER = 1
STYLE_DATETIME = 2
STYLE_NUMBER = 3

# Кодировка XML не знает управляющих символов; имена объектов приходят из чужого
# архива, поэтому чистим их, а не надеемся на источник.
_FORBIDDEN = {code: None for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)}


@dataclass(frozen=True, slots=True)
class Column:
    title: str
    kind: str = "text"  # text | datetime | number
    width: float = 16.0


def _escape(value: str) -> str:
    return (
        str(value)
        .translate(_FORBIDDEN)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _excel_serial(value: datetime | date) -> float:
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    return (value - EXCEL_EPOCH).total_seconds() / 86400.0


def _column_name(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _cell(ref: str, value: Any, kind: str) -> str:
    if value is None or value == "":
        return ""
    if kind == "datetime" and isinstance(value, (datetime, date)):
        return f'<c r="{ref}" s="{STYLE_DATETIME}"><v>{_excel_serial(value):.10f}</v></c>'
    if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{STYLE_NUMBER}"><v>{float(value):.6f}</v></c>'
    # Всё остальное (в том числе дата/число, пришедшие строкой) — текст: лучше
    # показать значение как есть, чем потерять его на неожиданном типе.
    return (
        f'<c r="{ref}" s="{STYLE_TEXT}" t="inlineStr">'
        f"<is><t xml:space=\"preserve\">{_escape(value)}</t></is></c>"
    )


def _sheet_xml(columns: Sequence[Column], rows: Sequence[Sequence[Any]]) -> str:
    last_column = _column_name(len(columns))
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        f'<dimension ref="A1:{last_column}{len(rows) + 1}"/>',
        # Заголовок закреплён: журнал выгружается на сотни строк, и без этого
        # при прокрутке колонки приходится угадывать.
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>",
        '<sheetFormatPr defaultRowHeight="15"/>',
        "<cols>",
    ]
    for index, column in enumerate(columns, start=1):
        parts.append(
            f'<col min="{index}" max="{index}" width="{column.width:.2f}" customWidth="1"/>'
        )
    parts.append("</cols><sheetData>")

    header_cells = "".join(
        f'<c r="{_column_name(index)}1" s="{STYLE_HEADER}" t="inlineStr">'
        f"<is><t>{_escape(column.title)}</t></is></c>"
        for index, column in enumerate(columns, start=1)
    )
    parts.append(f'<row r="1">{header_cells}</row>')

    for row_index, row in enumerate(rows, start=2):
        cells = "".join(
            _cell(f"{_column_name(index)}{row_index}", value, columns[index - 1].kind)
            for index, value in enumerate(row[: len(columns)], start=1)
        )
        parts.append(f'<row r="{row_index}">{cells}</row>')

    parts.append("</sheetData>")
    parts.append(f'<autoFilter ref="A1:{last_column}{len(rows) + 1}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# numFmtId 164/165 — пользовательские форматы (встроенные заканчиваются на 163).
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2">
<numFmt numFmtId="164" formatCode="DD.MM.YYYY\\ HH:MM:SS"/>
<numFmt numFmtId="165" formatCode="0.00"/>
</numFmts>
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
</styleSheet>"""


def build_workbook(
    columns: Sequence[Column],
    rows: Iterable[Sequence[Any]],
    *,
    sheet_name: str = "Лист1",
) -> bytes:
    """Собирает .xlsx с одним листом и возвращает его байтами."""
    materialized = [list(row) for row in rows]
    # Имя листа Excel ограничивает 31 символом и запрещает : \\ / ? * [ ]
    safe_name = _escape(sheet_name.translate({ord(char): None for char in r":\/?*[]"})[:31]) or "Лист1"
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_name}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )

    buffer = io.BytesIO()
    # Детерминированный архив: у всех частей одна дата, поэтому одинаковая
    # выгрузка даёт побайтово одинаковый файл (удобно и для тестов, и для
    # сравнения выгрузок между собой).
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", _CONTENT_TYPES),
            ("_rels/.rels", _ROOT_RELS),
            ("xl/workbook.xml", workbook_xml),
            ("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS),
            ("xl/styles.xml", _STYLES),
            ("xl/worksheets/sheet1.xml", _sheet_xml(columns, materialized)),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()
