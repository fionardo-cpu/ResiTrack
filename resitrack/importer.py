"""Import shipments from XLSX / XLS / CSV / HTML / XML (Shopee Seller Centre 'Pesanan Saya > Export')."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

from .couriers import detect_courier, normalize_resi
from .models import merge_products, parse_idr

# Header name mappings (case-insensitive, priority order within each list)
ALIASES: dict[str, list[str]] = {
    "resi": [
        "no. resi", "no resi", "nomor resi", "resi", "awb", "tracking number",
        "tracking_number", "no. resi pengiriman", "waybill", "tracking no",
        "no tracking", "resi pengiriman", "nomor tracking", "airway bill"
    ],
    "order_sn": [
        "no. pesanan", "no pesanan", "nomor pesanan", "order id", "order_sn",
        "order sn", "no. order", "id pesanan", "order no", "order_id"
    ],
    "buyer_username": [
        "username (pembeli)", "username pembeli", "username", "buyer username",
        "buyer_username", "pembeli", "buyer", "nama pengguna (pembeli)",
        "nama pembeli", "user buyer", "akun pembeli"
    ],
    "courier_hint": [
        "opsi pengiriman", "jasa kirim", "kurir", "courier", "ekspedisi",
        "shipping option", "shipping_carrier", "jasa pengiriman", "metode pengiriman",
        "layanan pengiriman", "channel pengiriman", "antar ke counter/ pick-up"
    ],
    "recipient_name": ["nama penerima", "penerima", "recipient", "recipient name", "nama tujuan", "nama pelanggan"],
    "recipient_phone": ["no. telepon", "no telepon", "telepon", "phone", "no. hp", "nomor telepon", "no hp", "nomor hp", "hp penerima", "kontak penerima"],
    "city": ["kota/kabupaten", "kota", "kabupaten", "city", "kota penerima", "kabupaten penerima"],
    "province": ["provinsi", "province", "propinsi"],
    "product": ["nama produk", "produk", "product", "product name", "item", "nama barang", "sku induk"],
    "variant": ["nama variasi", "variasi", "variant", "variation", "opsi variasi"],
    "note": ["catatan", "note", "catatan dari pembeli", "pesan", "catatan pesanan"],
    "order_status": ["status pesanan", "order status", "status order", "status"],
    "cancel_reason": [
        "alasan pembatalan", "cancel reason", "cancel_reason", "buyer_cancel_reason",
        "alasan batal", "keterangan batal",
    ],
    "cancel_by": ["dibatalkan oleh", "cancel by", "cancel_by", "cancelled by", "canceled by"],
    "payment_method": ["metode pembayaran", "payment method", "payment_method", "cara pembayaran"],
    "total_payment": ["total pembayaran", "total payment", "total_amount", "jumlah pembayaran"],
    "subtotal_item": ["subtotal pesanan", "subtotal produk", "subtotal"],
    "buyer_shipping": ["ongkos kirim dibayar oleh pembeli", "ongkir dibayar pembeli", "buyer shipping fee"],
    "return_shipping": ["ongkos kirim pengembalian barang", "ongkir pengembalian"],
    "shipping_discount": ["estimasi potongan biaya pengiriman", "potongan biaya pengiriman", "subsidi ongkir"],
    "shipping_est": ["perkiraan ongkos kirim", "estimated shipping fee", "ongkos kirim"],
    "seller_voucher": ["voucher ditanggung penjual", "voucher penjual"],
    "shopee_voucher": ["voucher ditanggung shopee", "voucher shopee"],
    "coins_discount": ["potongan koin shopee", "koin shopee", "coins discount"],
    "cc_discount": ["diskon kartu kredit", "credit card discount"],
}

TEMPLATE_HEADER = [
    "No. Resi", "Kurir", "No. Pesanan", "Username (Pembeli)", "Status Pesanan",
    "Nama Penerima", "Nama Produk", "Catatan"
]


class _HTMLTableExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._cur_row: list[str] = []
        self._cur_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("td", "th"):
            self._in_cell = True
            self._cur_cell = []
        elif t == "tr":
            self._cur_row = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("td", "th"):
            self._in_cell = False
            val = html.unescape("".join(self._cur_cell)).strip()
            self._cur_row.append(val)
        elif t == "tr":
            if any(self._cur_row):
                self.rows.append(self._cur_row)

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data)


def _clean_cell_str(val) -> str:
    """Format Excel/CSV cell value cleanly without trailing .0 or scientific notation on IDs."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        return f"{val:.2f}".rstrip("0").rstrip(".")
    s = str(val).strip()
    if re.fullmatch(r"-?\d+\.0", s):
        return s[:-2]
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+", s):
        try:
            d = Decimal(s)
            if d == d.to_integral_value():
                return str(int(d))
        except (InvalidOperation, ValueError, OverflowError):
            pass
    return s


def _col_letters_to_idx(ref: str) -> int:
    """Convert Excel cell coordinate like 'AS2' or 'F10' -> 0-based column index."""
    col = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            col = col * 26 + (ord(ch) - ord("A") + 1)
        elif "a" <= ch <= "z":
            col = col * 26 + (ord(ch) - ord("a") + 1)
        else:
            break
    return max(0, col - 1)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _read_xlsx_raw_zip(raw: bytes) -> list[tuple[str, list[list[str]]]]:
    """Pure-Python OOXML reader bypassing styles.xml, theme.xml, drawing.xml, and <dimension>.

    Shopee's exported .xlsx files often contain non-standard style XML or missing dimensions that
    crash openpyxl. Reading worksheets & sharedStrings directly via stdlib zipfile+ElementTree
    extracts cell data reliably across all sheets.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        entry_map = {name.lstrip("/").lower(): name for name in zf.namelist()}

        # 1. Shared strings table (optional)
        shared_strings: list[str] = []
        ss_key = entry_map.get("xl/sharedstrings.xml")
        if ss_key:
            with zf.open(ss_key) as f:
                for event, elem in ET.iterparse(f, events=("end",)):
                    if _local_tag(elem.tag) == "si":
                        parts = [
                            el.text
                            for el in elem.iter()
                            if _local_tag(el.tag) == "t" and el.text is not None
                        ]
                        shared_strings.append("".join(parts))
                        elem.clear()

        # 2. Worksheet target paths in workbook order
        rel_targets: dict[str, str] = {}
        rels_key = entry_map.get("xl/_rels/workbook.xml.rels")
        if rels_key:
            rels_root = ET.fromstring(zf.read(rels_key))
            for rel in rels_root:
                rid = rel.attrib.get("Id", "")
                tgt = rel.attrib.get("Target", "")
                if rid and tgt:
                    norm_tgt = tgt.lstrip("/").lower()
                    if not norm_tgt.startswith("xl/"):
                        norm_tgt = f"xl/{norm_tgt}"
                    rel_targets[rid] = norm_tgt

        sheets_to_read: list[tuple[str, str]] = []
        wb_key = entry_map.get("xl/workbook.xml")
        if wb_key:
            wb_root = ET.fromstring(zf.read(wb_key))
            for el in wb_root.iter():
                if _local_tag(el.tag) == "sheet":
                    name = el.attrib.get("name", "Sheet")
                    rid = next(
                        (v for k, v in el.attrib.items() if k == "r:id" or k.endswith("}id")),
                        "",
                    )
                    tgt = rel_targets.get(rid, "")
                    if tgt and tgt in entry_map:
                        sheets_to_read.append((name, entry_map[tgt]))

        if not sheets_to_read:
            ws_keys = sorted(
                (k for k in entry_map if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", k)),
                key=lambda s: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)],
            )
            sheets_to_read = [(k.rsplit("/", 1)[-1], entry_map[k]) for k in ws_keys]

        # 3. Parse each worksheet stream
        out: list[tuple[str, list[list[str]]]] = []
        for s_name, zip_name in sheets_to_read:
            rows: list[list[str]] = []
            with zf.open(zip_name) as f:
                for _ev, elem in ET.iterparse(f, events=("end",)):
                    if _local_tag(elem.tag) == "row":
                        row_cells: dict[int, str] = {}
                        next_col = 0
                        for c_el in elem:
                            if _local_tag(c_el.tag) != "c":
                                continue
                            ref = c_el.attrib.get("r", "")
                            col_idx = _col_letters_to_idx(ref) if ref else next_col
                            next_col = col_idx + 1
                            ctype = c_el.attrib.get("t", "")

                            val_str = ""
                            if ctype == "s":
                                v_el = next((ch for ch in c_el if _local_tag(ch.tag) == "v"), None)
                                if v_el is not None and v_el.text is not None:
                                    try:
                                        val_str = shared_strings[int(v_el.text.strip())]
                                    except (ValueError, IndexError):
                                        val_str = v_el.text
                            elif ctype == "inlineStr":
                                is_el = next((ch for ch in c_el if _local_tag(ch.tag) == "is"), None)
                                if is_el is not None:
                                    val_str = "".join(
                                        t_el.text
                                        for t_el in is_el.iter()
                                        if _local_tag(t_el.tag) == "t" and t_el.text is not None
                                    )
                            elif ctype == "b":
                                v_el = next((ch for ch in c_el if _local_tag(ch.tag) == "v"), None)
                                if v_el is not None and v_el.text is not None:
                                    val_str = "TRUE" if v_el.text.strip() == "1" else "FALSE"
                            else:
                                v_el = next((ch for ch in c_el if _local_tag(ch.tag) == "v"), None)
                                if v_el is not None and v_el.text is not None:
                                    val_str = _clean_cell_str(v_el.text)
                                else:
                                    # Check for <is> anyway in weird producers
                                    is_el = next((ch for ch in c_el if _local_tag(ch.tag) == "is"), None)
                                    if is_el is not None:
                                        val_str = "".join(
                                            t_el.text
                                            for t_el in is_el.iter()
                                            if _local_tag(t_el.tag) == "t" and t_el.text is not None
                                        )
                            row_cells[col_idx] = val_str.strip() if isinstance(val_str, str) else _clean_cell_str(val_str)

                        if row_cells and any(row_cells.values()):
                            max_col = max(row_cells.keys())
                            rows.append([row_cells.get(ci, "") for ci in range(max_col + 1)])
                        elem.clear()
            if rows:
                out.append((s_name, rows))
        return out


def _extract_html_table(text: str) -> list[list[str]]:
    parser = _HTMLTableExtractor()
    parser.feed(text)
    return parser.rows


def _extract_xml_spreadsheet(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    try:
        clean_text = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", text)
        root = ET.fromstring(clean_text)
        for row_elem in root.iter():
            if _local_tag(row_elem.tag) == "Row":
                row_cells: dict[int, str] = {}
                col_idx = 0
                for cell in row_elem:
                    if _local_tag(cell.tag) != "Cell":
                        continue
                    idx_attr = next((v for k, v in cell.attrib.items() if k == "Index" or k.endswith("}Index")), "")
                    if idx_attr.isdigit():
                        col_idx = max(0, int(idx_attr) - 1)
                    data_el = next((c for c in cell if _local_tag(c.tag) == "Data"), None)
                    row_cells[col_idx] = _clean_cell_str(data_el.text) if (data_el is not None and data_el.text) else ""
                    col_idx += 1
                if row_cells and any(row_cells.values()):
                    max_col = max(row_cells.keys())
                    rows.append([row_cells.get(ci, "") for ci in range(max_col + 1)])
    except Exception:
        pass
    return rows


def _read_csv_text(text: str) -> list[list[str]]:
    sample = text[:4096]
    counts = {",": sample.count(","), ";": sample.count(";"), "\t": sample.count("\t"), "|": sample.count("|")}
    best_delim = max(counts, key=counts.get) if any(counts.values()) else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = best_delim
    return [[_clean_cell_str(c) for c in r] for r in csv.reader(io.StringIO(text), dialect)]


def _read_sheets(path: Path) -> list[tuple[str, list[list[str]]]]:
    """Read file into [(sheet_name, rows), ...] using magic-byte detection + safe fallbacks."""
    try:
        raw = path.read_bytes()
    except PermissionError as e:
        raise ValueError(
            "File sedang dibuka di program lain (misalnya Microsoft Excel). "
            "Tutup file tersebut dulu lalu coba impor lagi."
        ) from e

    if not raw:
        return []

    # 1. ZIP container -> OOXML .xlsx / .xlsm (even if extension changed)
    if raw.startswith(b"PK\x03\x04"):
        errs: list[str] = []

        # 1a. Optional python_calamine (fastest tolerant Rust reader if available)
        try:
            from python_calamine import CalamineWorkbook  # type: ignore
            wb = CalamineWorkbook.from_filelike(io.BytesIO(raw))
            cal_sheets: list[tuple[str, list[list[str]]]] = []
            for s_name in wb.sheet_names:
                raw_rows = wb.get_sheet_by_name(s_name).to_python(skip_empty_area=False)
                str_rows = [[_clean_cell_str(c) for c in row] for row in raw_rows if any(c not in (None, "") for c in row)]
                if str_rows:
                    cal_sheets.append((s_name, str_rows))
            if cal_sheets:
                return cal_sheets
        except ImportError:
            pass
        except Exception as e:
            errs.append(f"calamine: {e}")

        # 1b. Stdlib raw OOXML ZIP+XML reader (immune to styles.xml/theme bugs!)
        try:
            raw_sheets = _read_xlsx_raw_zip(raw)
            if raw_sheets:
                return raw_sheets
        except Exception as e:
            errs.append(f"ooxml: {e}")

        # 1c. openpyxl fallbacks across ALL sheets
        try:
            from openpyxl import load_workbook
            for ro in (True, False):
                try:
                    wb = load_workbook(io.BytesIO(raw), read_only=ro, data_only=True)
                    ox_sheets: list[tuple[str, list[list[str]]]] = []
                    for ws in wb.worksheets:
                        str_rows = []
                        for row in ws.iter_rows(values_only=True):
                            clean = [_clean_cell_str(c) for c in row]
                            if any(clean):
                                str_rows.append(clean)
                        if str_rows:
                            ox_sheets.append((ws.title, str_rows))
                    wb.close()
                    if ox_sheets:
                        return ox_sheets
                except Exception as e:
                    errs.append(f"openpyxl(ro={ro}): {e}")
        except Exception as e:
            errs.append(f"openpyxl import: {e}")

        raise ValueError(f"File Excel (.xlsx) tidak dapat diurai ({' | '.join(errs) or 'sheet kosong'}).")

    # 2. OLE2 binary compound file -> legacy .xls (even if Shopee saved it with .xlsx extension)
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=raw)
            xls_sheets: list[tuple[str, list[list[str]]]] = []
            for ws in wb.sheets():
                rows = []
                for r in range(ws.nrows):
                    row_vals = [_clean_cell_str(ws.cell_value(r, c)) for c in range(ws.ncols)]
                    if any(row_vals):
                        rows.append(row_vals)
                if rows:
                    xls_sheets.append((ws.name, rows))
            if xls_sheets:
                return xls_sheets
        except Exception as e:
            msg = str(e).lower()
            if "encrypt" in msg or "password" in msg:
                raise ValueError(
                    "File Excel terkunci kata sandi (password-protected). "
                    "Buka di Excel dan simpan ulang tanpa password terlebih dahulu."
                ) from e
            raise ValueError(f"File Excel (.xls) tidak dapat dibaca: {e}") from e

    # 3. Text-based formats (HTML table, XML Spreadsheet 2003, CSV/TSV)
    text = ""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = raw.decode("latin-1", errors="replace")

    lower_head = text[:2048].lower()
    if "<html" in lower_head or "<table" in lower_head or "<tr" in lower_head:
        html_rows = _extract_html_table(text)
        if html_rows:
            return [("HTML", html_rows)]

    if "<?xml" in lower_head or "<workbook" in lower_head:
        xml_rows = _extract_xml_spreadsheet(text)
        if xml_rows:
            return [("XML", xml_rows)]

    csv_rows = _read_csv_text(text)
    return [("CSV", csv_rows)] if csv_rows else []


def _read_rows(path: Path) -> list[list[str]]:
    """Backwards-compatible helper returning rows from the first sheet."""
    sheets = _read_sheets(path)
    return sheets[0][1] if sheets else []


def _infer_cancel_by(cancel_by: str, cancel_reason: str) -> str:
    """Infer normalized cancel actor ('buyer'|'system'|'seller') from explicit column or Shopee cancel_reason text."""
    cb = (cancel_by or "").strip()
    cb_low = cb.lower()
    if cb_low:
        if cb_low in ("pembeli", "buyer"):
            return "buyer"
        if cb_low in ("sistem", "system", "shopee", "ops"):
            return "system"
        if cb_low in ("penjual", "seller"):
            return "seller"
        return cb
    cr_low = (cancel_reason or "").strip().lower()
    if not cr_low:
        return ""
    if any(k in cr_low for k in ("oleh sistem", "otomatis oleh sistem", "sistem shopee", "by system", "system cancel")):
        return "system"
    if any(k in cr_low for k in ("oleh pembeli", "by buyer", "dibatalkan pembeli")):
        return "buyer"
    if any(k in cr_low for k in ("oleh penjual", "by seller", "dibatalkan penjual")):
        return "seller"
    return ""


def _norm_header(h: str) -> str:
    """Normalize header by stripping quotes, newlines, tabs, and collapsing spaces."""
    s = str(h or "").strip().lower()
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r'["\']', "", s)
    return re.sub(r"\s+", " ", s).strip()


def _map_headers(headers: list[str]) -> dict[str, int]:
    norm = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}

    for field, names in ALIASES.items():
        for name in names:
            for i, h in enumerate(norm):
                if h and h == name and i not in mapping.values():
                    mapping[field] = i
                    break
            if field in mapping:
                break

    for field, names in ALIASES.items():
        if field in mapping:
            continue
        for name in names:
            if len(name) < 4:
                continue
            for i, h in enumerate(norm):
                if h and i not in mapping.values() and (name in h or (len(h) >= 4 and h in name)):
                    mapping[field] = i
                    break
            if field in mapping:
                break

    return mapping


def parse_file(path: str | Path) -> tuple[list[dict], list[str]]:
    """Return (records, warnings). Scans all sheets and deduplicates by both resi and order_sn."""
    path = Path(path)
    sheets = _read_sheets(path)
    warnings: list[str] = []

    if not sheets:
        raise ValueError("File kosong atau tidak berisi baris data.")

    # Search across ALL sheets (up to 50 rows each) for header row
    target_rows: list[list[str]] = []
    header_idx: int | None = None
    mapping: dict[str, int] = {}

    for _s_name, s_rows in sheets:
        for i, r in enumerate(s_rows[:50]):
            m = _map_headers(r)
            if "resi" in m or "order_sn" in m:
                target_rows = s_rows
                header_idx = i
                mapping = m
                break
        if header_idx is not None:
            break

    if header_idx is None:
        raise ValueError(
            "Kolom 'No. Resi' atau 'No. Pesanan' tidak ditemukan.\n"
            "Pastikan file yang diimport merupakan file export pesanan resmi Shopee atau format CSV/XLSX bertabel."
        )

    if "buyer_username" not in mapping:
        warnings.append("Kolom 'Username (Pembeli)' tidak ditemukan — resi tetap diimport tanpa username.")

    by_resi: dict[str, dict] = {}
    by_order_sn: dict[str, dict] = {}
    ordered_recs: list[dict] = []
    skipped = 0

    for r in target_rows[header_idx + 1:]:
        def g(field: str) -> str:
            i = mapping.get(field)
            if i is not None and i < len(r) and r[i] is not None:
                return _clean_cell_str(r[i])
            return ""

        raw_resi = g("resi")
        raw_order_sn = g("order_sn")

        # Skip repeated header rows if exports were concatenated
        if _norm_header(raw_resi) in ALIASES["resi"] or _norm_header(raw_order_sn) in ALIASES["order_sn"]:
            continue

        resi = normalize_resi(raw_resi)
        if resi in ("-", "NONE", "NULL", "NAN", "0"):
            resi = ""
        order_sn = raw_order_sn if raw_order_sn.upper() not in ("-", "NONE", "NULL", "NAN", "0") else ""

        if not resi and not order_sn:
            skipped += 1
            continue

        effective_resi = resi or f"ORDER-{order_sn}"
        raw_order_status = g("order_status")
        hint = g("courier_hint")
        courier = detect_courier(resi, hint) if resi else detect_courier("", hint)

        prod = g("product")
        var = g("variant")
        prod_full = f"{prod} ({var})" if (prod and var) else prod

        city = g("city")
        prov = g("province")
        city_full = f"{city}, {prov}" if (city and prov and prov.lower() not in city.lower()) else (city or prov)

        # Match existing record in file by either resi OR order_sn
        rec = (by_resi.get(resi) if resi else None) or (by_order_sn.get(order_sn) if order_sn else None) or by_resi.get(effective_resi)
        cancel_reason = g("cancel_reason")
        cancel_by = _infer_cancel_by(g("cancel_by"), cancel_reason)
        payment_method = g("payment_method")
        total_payment = parse_idr(g("total_payment"))
        sub_item = parse_idr(g("subtotal_item"))
        order_fee_vals = {
            k: parse_idr(g(k))
            for k in (
                "buyer_shipping", "shipping_est", "shipping_discount",
                "seller_voucher", "shopee_voucher", "coins_discount",
                "cc_discount", "return_shipping",
            )
        }
        if rec is None:
            fee_acc: dict[str, int] = {}
            if sub_item > 0:
                fee_acc["product_subtotal"] = sub_item
            for fk, fv in order_fee_vals.items():
                if fv > 0:
                    fee_acc[fk] = fv
            rec = {
                "resi": effective_resi,
                "courier": courier,
                "order_sn": order_sn,
                "buyer_username": g("buyer_username"),
                "recipient_name": g("recipient_name"),
                "recipient_phone": g("recipient_phone"),
                "city": city_full,
                "product": prod_full,
                "order_status": raw_order_status,
                "cancel_by": cancel_by,
                "cancel_reason": cancel_reason,
                "payment_method": payment_method,
                "total_payment": total_payment,
                "_fee_acc": fee_acc,
                "note": g("note"),
            }
            ordered_recs.append(rec)
            by_resi[effective_resi] = rec
            if resi:
                by_resi[resi] = rec
            if order_sn:
                by_order_sn[order_sn] = rec
        else:
            # Upgrade ORDER-{sn} placeholder to real resi if this row has one
            if resi and rec["resi"].startswith("ORDER-"):
                old_key = rec["resi"]
                rec["resi"] = resi
                by_resi.pop(old_key, None)
                by_resi[resi] = rec
            rec["product"] = merge_products(rec["product"], prod_full)
            fee_acc = rec["_fee_acc"]
            if sub_item > 0:
                fee_acc["product_subtotal"] = fee_acc.get("product_subtotal", 0) + sub_item
            for fk, fv in order_fee_vals.items():
                if fv > 0 and not fee_acc.get(fk):
                    fee_acc[fk] = fv
            if payment_method and not rec.get("payment_method"):
                rec["payment_method"] = payment_method
            if total_payment > 0 and not int(rec.get("total_payment") or 0):
                rec["total_payment"] = total_payment
            if raw_order_status and not rec.get("order_status"):
                rec["order_status"] = raw_order_status
            if cancel_by and not rec.get("cancel_by"):
                rec["cancel_by"] = cancel_by
            if cancel_reason and not rec.get("cancel_reason"):
                rec["cancel_reason"] = cancel_reason
            if city_full and not rec.get("city"):
                rec["city"] = city_full
            if courier and not rec.get("courier"):
                rec["courier"] = courier
            if g("buyer_username") and not rec.get("buyer_username"):
                rec["buyer_username"] = g("buyer_username")
            if g("recipient_name") and not rec.get("recipient_name"):
                rec["recipient_name"] = g("recipient_name")

    for rec in ordered_recs:
        fees = {k: v for k, v in rec.pop("_fee_acc", {}).items() if v > 0}
        tp = int(rec.get("total_payment") or 0)
        sub = fees.get("product_subtotal", 0)
        if tp > 0 and sub > 0:
            expected = (
                sub
                + fees.get("buyer_shipping", 0)
                - fees.get("seller_voucher", 0)
                - fees.get("shopee_voucher", 0)
                - fees.get("coins_discount", 0)
                - fees.get("cc_discount", 0)
            )
            if tp > expected:
                fees["service_fee"] = tp - expected
        rec["payment_details_json"] = json.dumps(fees, ensure_ascii=False) if fees else "{}"

    if skipped:
        warnings.append(f"{skipped} baris kosong dilewati.")

    unknown = [r["resi"] for r in ordered_recs if not r["courier"] and not r["resi"].startswith("ORDER-")]
    if unknown:
        warnings.append(f"{len(unknown)} resi kurirnya belum terdeteksi otomatis (bisa diatur lewat menu klik kanan).")

    return ordered_recs, warnings


def write_template(path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(TEMPLATE_HEADER)
        w.writerow(["SPXID041234567890", "SPX", "251120ABCDEF12", "buyer_contoh", "Sedang Dikirim", "Budi", "Kaos Polos (Hitam, L)", ""])
        w.writerow(["CM12345678901234", "JNE", "251120ABCDEF13", "pembeli2", "Selesai", "Sari", "Celana Jeans (Biru, 30)", ""])
        w.writerow(["11004703237411", "ANTERAJA", "251120ABCDEF14", "user_batal", "Batal", "Andi", "Topi Trucker", ""])


def export_csv(path: str | Path, rows) -> None:
    cols = [
        "resi", "courier", "order_sn", "buyer_username", "order_status", "status",
        "payment_method", "total_payment", "cancel_by", "cancel_reason",
        "last_event", "last_event_time", "recipient_name", "city", "product", "last_checked",
        "tracking_source", "note"
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] if c in r.keys() else "" for c in cols])
