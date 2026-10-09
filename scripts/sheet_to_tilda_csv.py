#!/usr/bin/env python3
"""
Convert a Google Sheet (CSV export) into Tilda Product Catalog import CSV.

Tilda import: Catalog → ⋮ → Import (or update) products from CSV
Docs: https://help.tilda.cc/online-store/import-export
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Tilda export/import column order (omit Tilda UID for new products)
TILDA_COLUMNS = [
    "Brand",
    "Brand SKU",
    "SKU",
    "Mark",
    "Category",
    "Title",
    "Description",
    "Text",
    "Photo",
    "Price",
    "Quantity",
    "Price OLD",
    "Editions",
    "Modifications",
    "External ID",
    "Parent UID",
    "Characteristics",
    "Height",
    "Width",
    "Length",
    "Weight",
    "SEO title",
    "SEO descr",
    "FB title",
    "FB descr",
    "Tabs",
    "Unit",
    "Portion",
    "URL",
]

# Map normalized header → Tilda field or "char:Name" for Characteristics:Name columns
DEFAULT_HEADER_MAP: dict[str, str] = {
    "title": "Title",
    "название": "Title",
    "name": "Title",
    "имя": "Title",
    "project": "Title",
    "проект": "Title",
    "description": "Description",
    "desc": "Description",
    "описание": "Description",
    "краткое описание": "Description",
    "text": "Text",
    "текст": "Text",
    "полное описание": "Text",
    "html": "Text",
    "price": "Price",
    "цена": "Price",
    "стоимость": "Price",
    "price old": "Price OLD",
    "старая цена": "Price OLD",
    "category": "Category",
    "категория": "Category",
    "photo": "Photo",
    "photos": "Photo",
    "image": "Photo",
    "images": "Photo",
    "фото": "Photo",
    "изображение": "Photo",
    "картинка": "Photo",
    "url фото": "Photo",
    "sku": "SKU",
    "артикул": "SKU",
    "external id": "External ID",
    "external_id": "External ID",
    "id": "External ID",
    "код": "External ID",
    "mark": "Mark",
    "метка": "Mark",
    "бейдж": "Mark",
    "seo title": "SEO title",
    "seo_title": "SEO title",
    "seo descr": "SEO descr",
    "seo description": "SEO descr",
    "seo_descr": "SEO descr",
    "url": "URL",
    "ссылка": "URL",
    "страница": "URL",
    "quantity": "Quantity",
    "остаток": "Quantity",
    "количество": "Quantity",
    # Common house-project attributes → Characteristics
    "площадь": "char:Площадь",
    "area": "char:Площадь",
    "этажность": "char:Этажность",
    "floors": "char:Этажность",
    "материал": "char:Материал стен",
    "material": "char:Материал стен",
    "размер": "char:Габариты",
    "габариты": "char:Габариты",
    "dimensions": "char:Габариты",
    "ширина": "char:Ширина",
    "width": "char:Ширина",
    "длина": "char:Длина",
    "length": "char:Длина",
    "высота": "char:Высота",
    "height": "char:Высота",
    "bedrooms": "char:Спальни",
    "спальни": "char:Спальни",
    "brand": "Brand",
    "бренд": "Brand",
}


def normalize_header(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower())


def detect_delimiter(sample: str) -> str:
    first_line = sample.split("\n", 1)[0]
    semi = first_line.count(";")
    comma = first_line.count(",")
    tab = first_line.count("\t")
    if semi >= comma and semi >= tab and semi > 0:
        return ";"
    if tab > comma:
        return "\t"
    return ","


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "vsedoma-tilda-import/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_sheet_rows(path: Path | None, url: str | None) -> tuple[list[str], list[dict[str, str]]]:
    text = fetch_text(url) if url else path.read_text(encoding="utf-8-sig")
    delim = detect_delimiter(text)
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    if not reader.fieldnames:
        raise SystemExit("Empty sheet or missing header row.")
    headers = [h.strip() for h in reader.fieldnames if h and h.strip()]
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append(row)
    return headers, rows


def resolve_mapping(headers: list[str]) -> dict[str, str]:
    """header name → target (Tilda field or char:Label)."""
    mapping: dict[str, str] = {}
    for h in headers:
        norm = normalize_header(h)
        if norm in DEFAULT_HEADER_MAP:
            mapping[h] = DEFAULT_HEADER_MAP[norm]
            continue
        if norm.startswith("characteristics:") or norm.startswith("характеристика:"):
            label = h.split(":", 1)[-1].strip()
            mapping[h] = f"char:{label}"
            continue
        # Pass through explicit Tilda column names
        for col in TILDA_COLUMNS:
            if norm == col.lower():
                mapping[h] = col
                break
        if h.startswith("Characteristics:"):
            mapping[h] = h  # already Tilda format
    return mapping


def format_characteristics(parts: list[str]) -> str:
    return "; ".join(p for p in parts if p)


def build_tilda_row(
    source: dict[str, str],
    mapping: dict[str, str],
    defaults: dict[str, str],
) -> dict[str, str]:
    out: dict[str, str] = {col: defaults.get(col, "") for col in TILDA_COLUMNS}
    char_parts: list[str] = []
    extra_char_cols: dict[str, str] = {}

    for src_col, value in source.items():
        if not value or src_col not in mapping:
            continue
        target = mapping[src_col]
        if target.startswith("char:"):
            label = target[5:]
            char_parts.append(f"{label}: {value}")
        elif target.startswith("Characteristics:"):
            extra_char_cols[target] = value
        else:
            out[target] = value

    if char_parts:
        existing = out.get("Characteristics", "")
        merged = format_characteristics(
            ([existing] if existing else []) + char_parts
        )
        out["Characteristics"] = merged

    # Photo: allow multiple URLs separated by comma/space/semicolon → Tilda uses space in one cell often
    if out.get("Photo"):
        urls = re.split(r"[\s,;]+", out["Photo"].strip())
        out["Photo"] = " ".join(u for u in urls if u.startswith("https://"))

    return out


def write_tilda_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TILDA_COLUMNS, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in TILDA_COLUMNS})


def google_sheet_export_url(sheet_id: str, gid: str | None) -> str:
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        base += f"&gid={gid}"
    return base


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Google Sheet → Tilda Product Catalog CSV")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="Local CSV exported from Google Sheets")
    src.add_argument("--sheet-url", help="Full Google Sheets export URL")
    src.add_argument("--sheet-id", help="Google Spreadsheet ID")
    p.add_argument("--gid", help="Worksheet gid (tab id from URL #gid=...)")
    p.add_argument("-o", "--output", type=Path, default=Path("tilda_products_import.csv"))
    p.add_argument("--default-category", default="", help="Category if sheet has no Category column")
    p.add_argument("--default-price", default="", help="Price if sheet has no Price column")
    p.add_argument("--default-quantity", default="", help="Blank = unlimited stock in Tilda")
    p.add_argument("--dry-run", action="store_true", help="Print mapping and row count only")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    url = args.sheet_url
    if args.sheet_id:
        url = google_sheet_export_url(args.sheet_id, args.gid)

    headers, rows = read_sheet_rows(args.input, url)
    mapping = resolve_mapping(headers)
    if not mapping:
        print("Warning: no columns matched. Rename headers or extend DEFAULT_HEADER_MAP.", file=sys.stderr)
        print("Sheet headers:", ", ".join(headers), file=sys.stderr)

    defaults = {
        "Category": args.default_category,
        "Price": args.default_price,
        "Quantity": args.default_quantity,
    }

    tilda_rows = [build_tilda_row(r, mapping, defaults) for r in rows]

    missing_title = sum(1 for r in tilda_rows if not r.get("Title"))
    if missing_title:
        print(f"Warning: {missing_title} rows without Title (required).", file=sys.stderr)

    if args.dry_run:
        print(f"Rows: {len(tilda_rows)}")
        print("Column mapping:")
        for h, t in sorted(mapping.items(), key=lambda x: x[0].lower()):
            print(f"  {h!r} → {t}")
        return

    write_tilda_csv(tilda_rows, args.output)
    print(f"Wrote {len(tilda_rows)} products → {args.output}")
    print("Tilda: Catalog → ⋮ → Import (or update) products from CSV")


if __name__ == "__main__":
    main()
