"""Offline tests: courier detection, parsers, importer, Shopee signing, and status resolver."""
import csv
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resitrack import couriers, importer  # noqa: E402
from resitrack.models import (  # noqa: E402
    ST_CANCELED,
    ST_COMPLETED,
    ST_DELIVERED,
    ST_OUT,
    ST_PENDING,
    ST_RTS_BALSIS,
    ST_RTS_REJECTED,
    ST_RTS_RETURN,
    ST_TRANSIT,
    TrackEvent,
    resolve_overall_status,
)
from resitrack.shopee_api import ShopeeClient  # noqa: E402


class FakeResp:
    def __init__(self, data, status=200, ctype="application/json"):
        self._d, self.status_code, self.headers = data, status, {"Content-Type": ctype}

    def json(self):
        return self._d


def test_detect():
    assert couriers.detect_courier("SPXID041234567890") == "SPX"
    assert couriers.detect_courier("x", "SPX Express") == "SPX"
    assert couriers.detect_courier("CM12345678901234", "JNE Reguler") == "JNE"
    assert couriers.detect_courier("10001234567890") == "ANTERAJA"
    assert couriers.detect_courier("P2311230123456") == "POS"
    assert couriers.detect_courier("SHPE261118F7B940021539") == "POS"
    assert couriers.detect_courier("x", "Anteraja Reguler") == "ANTERAJA"
    assert couriers.detect_courier("x", "POS Kargo") == "POS"
    assert couriers.detect_courier("x", "Pos Reguler") == "POS"


def test_importer_user_sample():
    p = os.path.join(os.path.dirname(__file__), "user_data_sample.csv")
    if os.path.exists(p):
        recs, warns = importer.parse_file(p)
        assert len(recs) == 78
        # Check first order
        first = next(r for r in recs if r["order_sn"] == "261028S0CXKJK2")
        assert first["buyer_username"] == "sheilla.17"
        assert first["courier"] == "SPX"
        assert first["resi"] == "SPXID06017101360A"
        assert "MasKo" in first["product"]
        # Check POS order
        pos_rec = next(r for r in recs if r["order_sn"] == "261118KDK8MDWR")
        assert pos_rec["buyer_username"] == "punhj63y21"
        assert pos_rec["courier"] == "POS"
        assert pos_rec["resi"] == "SHPE261118F7B940021539"
        # Check Anteraja order
        ant_rec = next(r for r in recs if r["order_sn"] == "261120RME9FU3Y")
        assert ant_rec["buyer_username"] == "otgzfumq9y"
        assert ant_rec["courier"] == "ANTERAJA"
        assert ant_rec["resi"] == "11004703237411"


def test_spx_parse():
    payload = {"retcode": 0, "data": {"sls_tracking_info": {"records": [
        {"display_flag": 1, "actual_time": 1764000000, "tracking_code": "F600", "milestone_code": 6,
         "buyer_description": "Paket sedang diantar", "current_location": {"location_name": "Jakarta Hub"}},
        {"display_flag": 1, "actual_time": 1763900000, "tracking_code": "F510", "milestone_code": 5,
         "buyer_description": "Tiba di hub"}]}}}
    with mock.patch.object(couriers._session, "get", return_value=FakeResp(payload)):
        r = couriers.track_spx("SPXID1")
    assert r.status == ST_OUT and len(r.events) == 2 and r.events[0].location == "Jakarta Hub"


def test_anteraja_parse():
    payload = {"status": 200, "info": "OK", "content": [{"awb": "100", "history": [
        {"timestamp": "2026-11-20 10:00:00", "message": {"id": "Paket diterima oleh YBS"}, "tracking_code": 250},
        {"timestamp": "2026-11-19 08:00:00", "message": {"id": "Paket dalam perjalanan"}, "tracking_code": 210}]}]}
    with mock.patch.object(couriers._session, "post", return_value=FakeResp(payload)):
        r = couriers.track_anteraja("100")
    assert r.status == ST_DELIVERED and r.last_time == "2026-11-20 10:00"


def test_binderbyte_parse():
    payload = {"status": 200, "data": {"summary": {"status": "ON PROCESS"}, "history": [
        {"date": "2026-11-21 09:00:00", "desc": "DEPARTED FROM TRANSIT [JAKARTA]", "location": "JAKARTA"}]}}
    with mock.patch.object(couriers._session, "get", return_value=FakeResp(payload)):
        r = couriers.track_binderbyte("CM1", "JNE", "key")
    assert r.status == ST_TRANSIT and r.source == "BinderByte"


def test_zpi_parse():
    payload = {
        "project": "logistics:cek-resi:track",
        "data": {
            "ok": True,
            "awb": "0114541900204500",
            "courier": "jne",
            "courierName": "JNE Express",
            "status": "Delivered",
            "delivered": True,
            "history": [
                {"date": "2026-11-22 14:00:00", "description": "DELIVERED TO [YBS]", "location": "JAKARTA"}
            ]
        }
    }
    with mock.patch.object(couriers._session, "get", return_value=FakeResp(payload)):
        r = couriers.track_zpi("0114541900204500", "JNE")
    assert r.status == ST_DELIVERED and "ZPI" in r.source and len(r.events) == 1


def test_resolve_status_completed():
    # Order status Selesai -> ST_COMPLETED (Dana cair)
    res = resolve_overall_status("Selesai", ST_DELIVERED, [TrackEvent("2026-11-20 10:00", "Paket terkirim")])
    assert res == ST_COMPLETED


def test_resolve_status_balsis():
    # BalSis: RTS occurred while package was still at sorting warehouse (never out for delivery!)
    events = [
        TrackEvent("2026-11-22 09:00", "Paket dalam proses retur ke penjual (RTS)", "Jakarta Hub"),
        TrackEvent("2026-11-21 14:00", "Paket tiba di Pusat Sortir", "Jakarta Sorting Center"),
        TrackEvent("2026-11-20 10:00", "Paket diserahkan ke kurir", "Drop Point"),
    ]
    res = resolve_overall_status("Dikirim", "RTS", events)
    assert res == ST_RTS_BALSIS


def test_resolve_status_rejected():
    # Ditolak Pembeli: Courier was assigned to deliver to buyer, then RTS
    events = [
        TrackEvent("2026-11-22 16:00", "Paket kembali ke penjual (RTS)", "Hub"),
        TrackEvent("2026-11-22 14:00", "Pengantaran gagal: Pembeli menolak paket / COD Ditolak", "Alamat Penerima"),
        TrackEvent("2026-11-22 09:00", "Kurir sedang mengantar paket ke alamat penerima", "Hub Tujuan"),
        TrackEvent("2026-11-21 14:00", "Paket tiba di Hub Tujuan", "Hub Tujuan"),
    ]
    res = resolve_overall_status("Dikirim", "RTS", events)
    assert res == ST_RTS_REJECTED


def test_resolve_status_return():
    # Pengembalian barang resmi dari pembeli
    events = [TrackEvent("2026-11-22 10:00", "Paket retur dikirim kembali ke penjual")]
    res = resolve_overall_status("Pengembalian Barang/Dana", "RTS", events)
    assert res == ST_RTS_RETURN


def test_resolve_status_canceled():
    # Pesanan dibatalkan
    res = resolve_overall_status("Batal", ST_PENDING, [])
    assert res == ST_CANCELED


def test_importer_shopee_export():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "Order.all.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["No. Pesanan", "Status Pesanan", "No. Resi", "Opsi Pengiriman", "Username (Pembeli)",
                    "Nama Penerima", "Nama Produk"])
        w.writerow(["251120AAA", "Dikirim", "SPXID0001112223", "SPX Express", "budi99", "Budi", "Kaos"])
        w.writerow(["251120AAA", "Dikirim", "SPXID0001112223", "SPX Express", "budi99", "Budi", "Topi"])
        w.writerow(["251120BBB", "Selesai", "CM9999888877776", "JNE Reguler", "sari_x", "Sari", "Celana"])
        w.writerow(["251120CCC", "Batal", "10001234567890", "Anteraja", "batal_user", "Andi", "Sepatu"])
    recs, warns = importer.parse_file(p)
    assert len(recs) == 3
    spx = next(r for r in recs if r["courier"] == "SPX")
    assert spx["buyer_username"] == "budi99" and "Topi" in spx["product"]
    assert any(r["courier"] == "JNE" and r["order_status"] == "Selesai" for r in recs)
    assert any(r["courier"] == "ANTERAJA" and r["order_status"] == "Batal" for r in recs)


def test_shopee_sign():
    c = ShopeeClient({"shopee_partner_id": "123", "shopee_partner_key": "abc", "shopee_redirect_url": "https://x"})
    url = c.auth_url()
    assert "partner_id=123" in url and "sign=" in url
    code, shop = ShopeeClient.parse_redirect("https://x/?code=abcd&shop_id=999")
    assert (code, shop) == ("abcd", "999")


def test_resolve_status_balsis_hub_terakhir():
    # Hub-level RTS without actual courier delivery dispatch must resolve to BalSis
    events = [
        TrackEvent("2026-11-24 10:00", "Paket dikembalikan ke penjual (RTS)", "Hub Terakhir"),
        TrackEvent("2026-11-23 14:00", "Paket tiba di hub terakhir untuk penyortiran", "Jakarta Hub"),
    ]
    res = resolve_overall_status("Sedang Dikirim", "RTS", events)
    assert res == ST_RTS_BALSIS


def test_zpi_5xx_clean_error():
    from resitrack.models import TrackingError

    class Err523Resp:
        status_code = 523
        text = "<html>cloudflare origin unreachable</html>"

    with mock.patch.object(couriers._session, "get", return_value=Err523Resp()):
        try:
            couriers.track_zpi("SHPE261118F7B940021539", "POS")
            assert False, "Expected TrackingError"
        except TrackingError as e:
            msg = str(e)
            assert "Gateway ZPI" in msg and "cloudflare" not in msg.lower()


def test_db_stale_and_settings_dialog():
    from resitrack.db import DB
    from PySide6.QtWidgets import QApplication
    from resitrack.dialogs import SettingsDialog

    tmp_db = os.path.join(tempfile.mkdtemp(), "test.db")
    db = DB(tmp_db)
    db.upsert({"resi": "SPXID123", "courier": "SPX", "order_status": "Sedang Dikirim"})
    assert len(db.stale_active_resis(60)) == 1
    db.set_archived(["SPXID123"], True)
    assert db.archived_count() == 1
    assert len(db.stale_active_resis(60)) == 0

    _app = QApplication.instance() or QApplication([])
    dlg = SettingsDialog({"theme": "light"}, lambda s: None)
    res = dlg.result_settings()
    assert res["theme"] == "light"


def test_importer_shopee_corrupt_styles_and_inlinestr_xlsx():
    import zipfile

    p = os.path.join(tempfile.mkdtemp(), "shopee_broken_styles.xlsx")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        '<sheet name="Ringkasan" sheetId="1" r:id="rId1"/>'
        '<sheet name="orders" sheetId="2" r:id="rId2"/>'
        '</sheets>'
        '</workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '</Relationships>'
    )
    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Catatan Toko</t></is></c></row></sheetData>'
        '</worksheet>'
    )
    # Sheet2 uses sparse column coordinates (A, B, D, F, G, H) and inlineStr (no dimension, corrupt styles.xml)
    sheet2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row r="1">'
        '  <c r="A1" t="inlineStr"><is><t>No. Pesanan</t></is></c>'
        '  <c r="B1" t="inlineStr"><is><t>Status Pesanan</t></is></c>'
        '  <c r="D1" t="inlineStr"><is><t>No. Resi</t></is></c>'
        '  <c r="F1" t="inlineStr"><is><t>Opsi Pengiriman</t></is></c>'
        '  <c r="G1" t="inlineStr"><is><t>Nama Produk</t></is></c>'
        '  <c r="AS1" t="inlineStr"><is><t>Username (Pembeli)</t></is></c>'
        '</row>'
        '<row r="2">'
        '  <c r="A2" t="inlineStr"><is><t>261125ORDER1</t></is></c>'
        '  <c r="B2" t="inlineStr"><is><t>Sedang Dikirim</t></is></c>'
        '  <c r="D2" t="inlineStr"><is><t>SPXID06999111222B</t></is></c>'
        '  <c r="F2" t="inlineStr"><is><t>SPX Hemat</t></is></c>'
        '  <c r="G2" t="inlineStr"><is><t>Sarung Instan Premium</t></is></c>'
        '  <c r="AS2" t="inlineStr"><is><t>buyer_shopee_99</t></is></c>'
        '</row>'
        '<row r="3">'
        '  <c r="A3" t="inlineStr"><is><t>261125ORDER2</t></is></c>'
        '  <c r="B3" t="inlineStr"><is><t>Sedang Dikirim</t></is></c>'
        '  <c r="D3"><v>1.1004703237411E+13</v></c>'
        '  <c r="F3" t="inlineStr"><is><t>Anteraja Economy</t></is></c>'
        '  <c r="G3" t="inlineStr"><is><t>Peci Rajut</t></is></c>'
        '  <c r="AS3" t="inlineStr"><is><t>andi_jaya</t></is></c>'
        '</row>'
        '</sheetData>'
        '</worksheet>'
    )
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", "<corrupt_not_valid_styles_xml")
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)

    recs, warns = importer.parse_file(p)
    assert len(recs) == 2
    assert recs[0]["resi"] == "SPXID06999111222B" and recs[0]["buyer_username"] == "buyer_shopee_99"
    assert recs[0]["product"] == "Sarung Instan Premium"
    assert recs[1]["resi"] == "11004703237411" and recs[1]["courier"] == "ANTERAJA"


def test_importer_ole2_xls_magic_bytes():
    p = os.path.join(tempfile.mkdtemp(), "fake_ole2.xlsx")
    with open(p, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1corrupt_ole2_payload")
    try:
        importer.parse_file(p)
        assert False, "Expected ValueError on corrupt OLE2"
    except ValueError as e:
        # Must NOT misleadingly fail as CSV header missing
        assert ".xls" in str(e)


def test_importer_duplicate_rows_and_repeated_header():
    p = os.path.join(tempfile.mkdtemp(), "dups.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["No. Pesanan", "Status Pesanan", "No. Resi", "Opsi Pengiriman", "Username (Pembeli)", "Nama Produk", "Nama Variasi"])
        # Order 1 without resi first, then repeated header, then Order 1 with real resi, then exact duplicate
        w.writerow(["261125DUP1", "Perlu Dikirim", "", "SPX Hemat", "user_a", "Produk X", "Hitam"])
        w.writerow(["No. Pesanan", "Status Pesanan", "No. Resi", "Opsi Pengiriman", "Username (Pembeli)", "Nama Produk", "Nama Variasi"])
        w.writerow(["261125DUP1", "Sedang Dikirim", "SPXID06111222333B", "SPX Hemat", "user_a", "Produk X", "Hitam"])
        w.writerow(["261125DUP1", "Sedang Dikirim", "SPXID06111222333B", "SPX Hemat", "user_a", "Produk X", "Putih"])
    recs, _ = importer.parse_file(p)
    assert len(recs) == 1
    assert recs[0]["resi"] == "SPXID06111222333B"
    assert recs[0]["product"] == "Produk X (Hitam · Putih)"


def test_db_dedup_reimport_and_no_status_downgrade():
    from resitrack.db import DB

    tmp_db = os.path.join(tempfile.mkdtemp(), "dedup.db")
    db = DB(tmp_db)

    # 1. Insert placeholder ORDER-SN1
    assert db.upsert({
        "resi": "ORDER-SN1", "order_sn": "SN1", "courier": "SPX",
        "buyer_username": "budi", "product": "Sarung (Hitam)", "order_status": "Perlu Dikirim",
    }) == "new"

    # 2. Import real resi for same SN1 -> must upgrade in-place (still 1 row!)
    assert db.upsert({
        "resi": "SPXID06999888777B", "order_sn": "SN1", "courier": "SPX",
        "buyer_username": "budi", "product": "Sarung (Hitam)", "order_status": "Selesai",
    }) == "updated"
    assert len(db.all()) == 1
    r = db.get("SPXID06999888777B")
    assert r is not None and r["status"] == ST_COMPLETED

    # 3. Re-import exact same record -> must return 'same'
    assert db.upsert({
        "resi": "SPXID06999888777B", "order_sn": "SN1", "courier": "SPX",
        "buyer_username": "budi", "product": "Sarung (Hitam)", "order_status": "Selesai",
    }) == "same"

    # 4. Re-import older export (placeholder + older status 'Sedang Dikirim') -> must skip & NOT downgrade!
    res_old = db.upsert({
        "resi": "ORDER-SN1", "order_sn": "SN1", "courier": "SPX",
        "buyer_username": "budi", "product": "Sarung (Hitam)", "order_status": "Sedang Dikirim",
    })
    assert res_old == "same"
    assert len(db.all()) == 1
    assert db.get("SPXID06999888777B")["status"] == ST_COMPLETED


def test_collapsible_inspector_reopens_on_click():
    from PySide6.QtWidgets import QApplication
    import resitrack.config as cfg
    import resitrack.db as db_mod
    import resitrack.main_window as mw_mod

    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "ui_test.db"
    settings_path = tmp / "settings.json"
    settings_path.write_text('{"theme": "light", "inspector_open": true}', encoding="utf-8")

    old_cfg_db, old_cfg_set = cfg.DB_PATH, cfg.SETTINGS_PATH
    old_load, old_save = mw_mod.load_settings, mw_mod.save_settings
    try:
        cfg.DB_PATH = db_path
        cfg.SETTINGS_PATH = settings_path
        mw_mod.load_settings = lambda: {"theme": "light", "inspector_open": True}
        mw_mod.save_settings = lambda s: None

        db = db_mod.DB(db_path)
        db.upsert({
            "resi": "SPXID06000011122B", "courier": "SPX", "order_sn": "SN_UI_1",
            "buyer_username": "test_buyer", "order_status": "Selesai", "status": ST_COMPLETED,
        })

        _app = QApplication.instance() or QApplication([])
        win = mw_mod.MainWindow(auto_sync=False)
        win.show()
        assert win.inspector.isVisible()

        # Close inspector via header/close action
        win.close_inspector(save=False)
        assert win.inspector.isHidden()

        # Clicking an order row (even already selected) must re-open inspector
        win.table.selectRow(0)
        win._on_table_clicked(win.proxy.index(0, 0))
        assert win.inspector.isVisible()
        win.close()
    finally:
        cfg.DB_PATH, cfg.SETTINGS_PATH = old_cfg_db, old_cfg_set
        mw_mod.load_settings, mw_mod.save_settings = old_load, old_save


def test_db_clear_all():
    import resitrack.db as db_mod

    tmp = Path(tempfile.mkdtemp())
    db = db_mod.DB(tmp / "clear_test.db")
    db.upsert({"resi": "SPX1", "order_sn": "SN1", "courier": "SPX"})
    db.upsert({"resi": "SPX2", "order_sn": "SN2", "courier": "SPX"})
    db.upsert({"resi": "SPX3", "order_sn": "SN3", "courier": "SPX"})
    db.set_archived(["SPX3"], True)

    assert len(db.all(include_archived=True)) == 3
    removed_active = db.clear_all(include_archived=False)
    assert removed_active == 2
    assert len(db.all(include_archived=True)) == 1

    removed_rest = db.clear_all(include_archived=True)
    assert removed_rest == 1
    assert len(db.all(include_archived=True)) == 0


def test_resolve_preserves_explicit_rts_rejected_without_events():
    res = resolve_overall_status("Sedang Dikirim", ST_RTS_REJECTED, [])
    assert res == ST_RTS_REJECTED


def test_add_dialog_skips_invalid_lines_and_db_skips_placeholder_active():
    from PySide6.QtWidgets import QApplication
    from resitrack.db import DB
    from resitrack.dialogs import AddDialog

    _app = QApplication.instance() or QApplication([])
    dlg = AddDialog()
    dlg.text.setPlainText("---\n   \nSPXID041234567890, buyer_ok, SN99\n")
    recs = dlg.records()
    assert len(recs) == 1 and recs[0]["resi"] == "SPXID041234567890"
    dlg.close()

    tmp_db = os.path.join(tempfile.mkdtemp(), "ph_active.db")
    db = DB(tmp_db)
    db.upsert({"resi": "ORDER-SN1", "order_sn": "SN1", "courier": "SPX"})
    db.upsert({"resi": "SPXID1234567890", "order_sn": "SN2", "courier": "SPX"})
    active = db.active_resis()
    assert len(active) == 1 and active[0]["resi"] == "SPXID1234567890"
    db.close()


def test_shopee_client_invalid_partner_id_and_thread_lock():
    from resitrack.shopee_api import ShopeeError

    c = ShopeeClient({"shopee_partner_id": "abc_not_int", "shopee_partner_key": "secret"})
    try:
        _ = c.partner_id
        assert False, "Expected ShopeeError"
    except ShopeeError:
        pass
    assert hasattr(c, "_token_lock")


def test_ui_layout_no_squashing_or_header_overlap():
    from PySide6.QtWidgets import QApplication, QTabWidget
    import resitrack.config as cfg
    import resitrack.db as db_mod
    import resitrack.main_window as mw_mod
    from resitrack.dialogs import SettingsDialog

    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "layout_test.db"
    settings_path = tmp / "settings.json"
    settings_path.write_text('{"theme": "light", "inspector_open": true}', encoding="utf-8")

    old_cfg_db, old_cfg_set = cfg.DB_PATH, cfg.SETTINGS_PATH
    old_load, old_save = mw_mod.load_settings, mw_mod.save_settings
    try:
        cfg.DB_PATH = db_path
        cfg.SETTINGS_PATH = settings_path
        mw_mod.load_settings = lambda: dict(cfg.DEFAULTS)
        mw_mod.save_settings = lambda s: None

        db = db_mod.DB(db_path)
        db.upsert({
            "resi": "SPXID06999111222A", "courier": "SPX", "order_sn": "SN_ERR_1",
            "buyer_username": "err_buyer", "order_status": "Sedang Dikirim",
        })
        db.save_tracking("SPXID06999111222A", error="Gateway ZPI sedang gangguan sementara (HTTP 523).")
        db.close()

        app = QApplication.instance() or QApplication([])
        win = mw_mod.MainWindow(auto_sync=False)
        win.resize(1160, 720)
        win.show()
        app.processEvents()

        # Left sidebar must have NO QScrollArea and all items fit cleanly
        from PySide6.QtWidgets import QScrollArea
        rail = win.findChild(mw_mod.QFrame, "LeftRail")
        assert rail is not None and not rail.findChildren(QScrollArea)
        assert win.btn_theme.geometry().bottom() <= rail.height()

        # Header Tier-1 widgets at minimum window size must never overlap horizontally
        hdr_btns = [win._lbl_view_count, win.btn_import, win.btn_batch_track, win.btn_add, win.btn_toggle_inspector, win.btn_more]
        for a, b in zip(hdr_btns, hdr_btns[1:]):
            assert a.geometry().right() < b.geometry().left(), f"Overlap between {a} and {b}"

        # Header Tier-2 widgets (SegmentedBar, Search, Filter Button) must never overlap
        tier2 = [win._seg_other.parentWidget(), win.search, win.btn_filter]
        for a, b in zip(tier2, tier2[1:]):
            assert a.geometry().right() < b.geometry().left(), f"Tier-2 overlap between {a} and {b}"

        # Sidebar error count must route to Lainnya and match filtered proxy count cleanly
        assert win._seg_active.text() == "Berjalan · 0"
        assert win._seg_done.text() == "Selesai · 0"
        assert win._seg_other.text() == "Lainnya · 1"
        assert win._err_nav_btn.isVisible()
        win._on_nav_clicked("lainnya", "Error")
        app.processEvents()
        assert win.proxy.rowCount() == 1

        # SettingsDialog inputs across all tabs must never squash below 34px
        sd = SettingsDialog(win.settings, lambda s: win._shopee(s), win)
        sd.show()
        tw = sd.findChild(QTabWidget)
        for idx in range(tw.count()):
            tw.setCurrentIndex(idx)
            app.processEvents()
        for widget_name, widget in [
            ("theme_combo", sd.theme_combo), ("auto", sd.auto), ("delay", sd.delay),
            ("bb_key", sd.bb_key), ("pid", sd.pid), ("pkey", sd.pkey),
            ("redirect", sd.redirect), ("redirect_paste", sd.redirect_paste),
        ]:
            assert widget.height() >= 34, f"{widget_name} squashed to height={widget.height()}"
        sd.close()
        win.close()
    finally:
        cfg.DB_PATH, cfg.SETTINGS_PATH = old_cfg_db, old_cfg_set
        mw_mod.load_settings, mw_mod.save_settings = old_load, old_save


def test_lifecycle_categorization_berjalan_vs_selesai():
    from resitrack.db import DB
    from resitrack.models import CAT_ACTIVE, CAT_FINISHED, TrackResult, row_lifecycle

    assert ST_RTS_BALSIS == "Gagal - Balsis"
    assert ST_RTS_REJECTED == "Gagal - Ditolak"

    # "Pengembalian Selesai" must stay Pengembalian (not Selesai / Dana Cair), and resolve to 'selesai'
    st_ret = resolve_overall_status("Pengembalian Selesai", ST_RTS_RETURN, [])
    assert st_ret == ST_RTS_RETURN

    tmp_db = os.path.join(tempfile.mkdtemp(), "lifecycle.db")
    db = DB(tmp_db)
    # Seed legacy status labels and check _migrate normalizes them cleanly
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, status, order_status) VALUES (?, ?, ?, ?, ?)",
        ("SPX_LEG_1", "SPX", "SN_L1", "Pengiriman Gagal - BalSis", "Sedang Dikirim"),
    )
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, status, order_status) VALUES (?, ?, ?, ?, ?)",
        ("SPX_LEG_2", "SPX", "SN_L2", "Pengiriman Gagal - Ditolak Pembeli", "Sedang Dikirim"),
    )
    db.conn.commit()
    db._migrate()
    assert db.get("SPX_LEG_1")["status"] == ST_RTS_BALSIS
    assert db.get("SPX_LEG_2")["status"] == ST_RTS_REJECTED

    # Balsis still in return transit -> berjalan + included in active_resis()
    assert row_lifecycle(dict(db.get("SPX_LEG_1"))) == CAT_ACTIVE
    active_ids = {r["resi"] for r in db.active_resis()}
    assert "SPX_LEG_1" in active_ids and "SPX_LEG_2" in active_ids

    # Tracking updates with "Paket telah dikembalikan ke penjual" -> automatically moves to selesai + excluded from active_resis
    db.save_tracking(
        "SPX_LEG_1",
        TrackResult(
            resi="SPX_LEG_1",
            courier="SPX",
            status="RTS",
            events=[
                TrackEvent("2026-11-26 10:00", "Paket telah dikembalikan ke penjual", "Jakarta"),
                TrackEvent("2026-11-25 14:00", "Paket dikembalikan ke penjual (RTS)", "Gudang Sortir"),
            ],
        ),
    )
    assert row_lifecycle(dict(db.get("SPX_LEG_1"))) == CAT_FINISHED
    assert "SPX_LEG_1" not in {r["resi"] for r in db.active_resis()}

    # Auto lifecycle transitions from real courier return-delivered event (no manual toggle!)
    db.save_tracking(
        "SPX_LEG_2",
        TrackResult(
            resi="SPX_LEG_2",
            courier="SPX",
            status="RTS",
            events=[
                TrackEvent("2026-11-26 11:00", "Paket telah dikembalikan ke pengirim", "Jakarta"),
                TrackEvent("2026-11-25 14:00", "Kurir sedang mengantar paket ke alamat penerima", "Jakarta"),
            ],
        ),
    )
    assert db.get("SPX_LEG_2")["status"] == ST_RTS_REJECTED
    assert row_lifecycle(dict(db.get("SPX_LEG_2"))) == CAT_FINISHED
    assert "SPX_LEG_2" not in {r["resi"] for r in db.active_resis()}

    # Completed and Canceled -> selesai; Delivered (unsettled) -> berjalan
    db.upsert({"resi": "SPX_DONE", "order_sn": "SN_D", "courier": "SPX", "order_status": "Selesai"})
    db.upsert({"resi": "SPX_CANC", "order_sn": "SN_C", "courier": "SPX", "order_status": "Batal"})
    db.upsert({"resi": "SPX_DELV", "order_sn": "SN_V", "courier": "SPX", "status": ST_DELIVERED, "order_status": "Telah Dikirim"})
    assert row_lifecycle(dict(db.get("SPX_DONE"))) == CAT_FINISHED
    assert row_lifecycle(dict(db.get("SPX_CANC"))) == CAT_FINISHED
    assert row_lifecycle(dict(db.get("SPX_DELV"))) == CAT_ACTIVE
    db.close()


def test_inline_copy_resi_hit_rect_and_clipboard():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    import resitrack.config as cfg
    import resitrack.db as db_mod
    import resitrack.main_window as mw_mod

    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "copy_test.db"
    old_cfg_db, old_cfg_set = cfg.DB_PATH, cfg.SETTINGS_PATH
    old_load, old_save = mw_mod.load_settings, mw_mod.save_settings
    try:
        cfg.DB_PATH = db_path
        cfg.SETTINGS_PATH = tmp / "settings.json"
        mw_mod.load_settings = lambda: dict(cfg.DEFAULTS)
        mw_mod.save_settings = lambda s: None

        db = db_mod.DB(db_path)
        db.upsert({"resi": "SPXID06123456789B", "courier": "SPX", "order_sn": "SN_COPY_1", "order_status": "Sedang Dikirim"})
        db.close()

        _app = QApplication.instance() or QApplication([])
        win = mw_mod.MainWindow(auto_sync=False)
        win.show()

        cell_rect = QRect(0, 0, 232, 60)
        btn_rect = mw_mod.ResiCellDelegate.copy_button_rect(cell_rect, "SPXID06123456789B")
        assert not btn_rect.isEmpty() and cell_rect.contains(btn_rect)

        win._copy_resi_inline("SPXID06123456789B")
        assert QGuiApplication.clipboard().text() == "SPXID06123456789B"
        assert win.table.copied_resi == "SPXID06123456789B"
        win.close()
    finally:
        cfg.DB_PATH, cfg.SETTINGS_PATH = old_cfg_db, old_cfg_set
        mw_mod.load_settings, mw_mod.save_settings = old_load, old_save


def test_canceled_buyer_vs_system_classification_and_import():
    from resitrack.db import DB
    from resitrack.models import (
        CAT_FINISHED,
        ST_CANCELED_BUYER,
        ST_CANCELED_SYSTEM,
        row_lifecycle,
    )

    assert resolve_overall_status(
        "Batal", "", [], cancel_by="buyer", cancel_reason="Ingin mengubah alamat pengiriman"
    ) == ST_CANCELED_BUYER
    assert resolve_overall_status(
        "Batal", "", [], cancel_by="system", cancel_reason="Terdeteksi melakukan aktivitas tidak wajar"
    ) == ST_CANCELED_SYSTEM
    assert resolve_overall_status(
        "Dibatalkan", "", [], cancel_by="", cancel_reason="Pesanan dibatalkan otomatis oleh sistem karena aktivitas kurang wajar"
    ) == ST_CANCELED_SYSTEM

    p = os.path.join(tempfile.mkdtemp(), "canceled.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["No. Pesanan", "Status Pesanan", "No. Resi", "Dibatalkan Oleh", "Alasan Pembatalan", "Username (Pembeli)"])
        w.writerow(["261126C1", "Batal", "", "Pembeli", "Ingin mengubah alamat pengiriman", "buyer_batal"])
        w.writerow(["261126C2", "Batal", "", "Sistem", "Terdeteksi melakukan aktivitas tidak wajar", "akun_sus"])

    recs, _ = importer.parse_file(p)
    assert len(recs) == 2

    tmp_db = os.path.join(tempfile.mkdtemp(), "cancel_test.db")
    db = DB(tmp_db)
    for rec in recs:
        db.upsert(rec)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, status, order_status, cancel_by, cancel_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ORDER-OLD", "SPX", "OLD", "Canceled", "Batal", "system", "Aktivitas tidak wajar"),
    )
    db.conn.commit()
    db._migrate()

    r1 = dict(db.get("ORDER-261126C1"))
    r2 = dict(db.get("ORDER-261126C2"))
    r_old = dict(db.get("ORDER-OLD"))
    assert r1["status"] == ST_CANCELED_BUYER and row_lifecycle(r1) == CAT_FINISHED
    assert r1["cancel_reason"] == "Ingin mengubah alamat pengiriman"
    assert r2["status"] == ST_CANCELED_SYSTEM and row_lifecycle(r2) == CAT_FINISHED
    assert r_old["status"] == ST_CANCELED_SYSTEM

    # ShopeeClient.sync preserves canceled orders without tracking number using ORDER-{sn}
    c = ShopeeClient({"shopee_partner_id": "1", "shopee_partner_key": "k", "shopee_refresh_token": "r", "shopee_shop_id": "2"})
    with mock.patch.object(c, "order_list", return_value=["SN_CANC"]), \
         mock.patch.object(c, "order_details", return_value=[{
             "order_sn": "SN_CANC",
             "order_status": "CANCELLED",
             "cancel_by": "system",
             "cancel_reason": "Unusual activity detected",
             "buyer_username": "x",
             "shipping_carrier": "SPX",
             "package_list": [{}],
             "item_list": [
                 {"item_name": "Sarung", "model_name": "Hitam", "model_quantity_purchased": 1},
                 {"item_name": "Sarung", "model_name": "Putih", "model_quantity_purchased": 2},
             ],
         }]), \
         mock.patch.object(c, "tracking_number", return_value=""):
        synced = c.sync(7)
    assert len(synced) == 1
    assert synced[0]["resi"] == "ORDER-SN_CANC"
    assert synced[0]["cancel_by"] == "system"
    assert synced[0]["product"] == "Sarung (Hitam · Putih x2)"
    db.close()


def test_product_variant_grouping_minimalist():
    from resitrack.db import DB
    from resitrack.models import merge_products

    grouped = merge_products(
        "MasKo - Sarung Instan (Hitam,Standar)",
        "MasKo - Sarung Instan (Putih,Standar); Peci Rajut (All Size)",
    )
    assert grouped == "MasKo - Sarung Instan (Hitam,Standar · Putih,Standar); Peci Rajut (All Size)"
    # Re-merging an already present variant is idempotent
    assert merge_products(grouped, "MasKo - Sarung Instan (Hitam,Standar)") == grouped

    tmp_db = os.path.join(tempfile.mkdtemp(), "prod_group.db")
    db = DB(tmp_db)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, product) VALUES (?, ?, ?, ?)",
        ("SPX_P1", "SPX", "SN_P1", "MasKo - Sarung (Hitam,Standar); MasKo - Sarung (Putih,Standar)"),
    )
    db.conn.commit()
    db._migrate()
    assert db.get("SPX_P1")["product"] == "MasKo - Sarung (Hitam,Standar · Putih,Standar)"
    assert db.upsert({
        "resi": "SPX_P1", "courier": "SPX", "order_sn": "SN_P1", "product": "MasKo - Sarung (Hitam,Standar)",
    }) == "same"
    db.close()


def test_spx_real_rts_completion_and_postponement_and_delivered():
    from resitrack.db import DB
    from resitrack.models import CAT_ACTIVE, CAT_FINISHED, TrackResult, row_lifecycle

    # 1. SPX F999 "Pesanan dikembalikan ke Agen / Pengirim" without delivery attempt -> Gagal - Balsis + Selesai
    evs_balsis_done = [
        TrackEvent("2026-10-26 15:10", "Pesanan dikembalikan ke Agen / Pengirim", "Kayugeritan First Mile Hub"),
        TrackEvent("2026-10-26 15:08", "Pesanan sedang dikembalikan ke alamat Pengirim.", "Kayugeritan First Mile Hub"),
        TrackEvent("2026-10-21 09:17", "Pesanan diproses di lokasi sortir Kota Surabaya, Surabaya DC.", "Surabaya DC"),
    ]
    st_b = resolve_overall_status("Batal", "RETUR", evs_balsis_done)
    assert st_b == ST_RTS_BALSIS
    assert row_lifecycle({"status": st_b, "order_status": "Batal", "events_json": '[{"description":"Pesanan dikembalikan ke Agen / Pengirim","location":"Kayugeritan First Mile Hub"}]'}) == CAT_FINISHED

    # 2. SPX F999 after buyer rejection -> Gagal - Ditolak + Selesai
    evs_rej_done = [
        TrackEvent("2026-11-21 17:57", "Pesanan dikembalikan ke Agen / Pengirim", "Kedungwuni 3 First Mile Hub"),
        TrackEvent("2026-11-21 16:50", "Pesanan sedang dikembalikan ke alamat Pengirim.", "Kedungwuni 3 First Mile Hub"),
        TrackEvent("2026-11-18 10:34", "Pengiriman ditunda karena Pesanan ditolak pembeli. Pesanan akan dikembalikan ke pengirim.", "Coblong Hub"),
        TrackEvent("2026-11-18 09:01", "Pesanan dalam proses pengantaran.", "Coblong Hub"),
    ]
    st_r = resolve_overall_status("Batal", "RETUR", evs_rej_done)
    assert st_r == ST_RTS_REJECTED
    import json as _json
    assert row_lifecycle({"status": st_r, "order_status": "Batal", "events_json": _json.dumps([e.__dict__ for e in evs_rej_done])}) == CAT_FINISHED

    # 3. Still in return transit F680 ("Pesanan sedang dikembalikan ke alamat Pengirim.") -> Gagal - Balsis + Berjalan
    evs_balsis_active = [
        TrackEvent("2026-11-21 17:43", "Pesanan sedang dikembalikan ke alamat Pengirim.", "Kedungwuni 4 First Mile Hub"),
        TrackEvent("2026-11-11 04:38", "Pengembalian Pesanan sedang dalam perjalanan menuju lokasi selanjutnya Maros DC.", ""),
        TrackEvent("2026-11-08 05:18", "Pesanan diproses di lokasi sortir Kab. Maros, Maros DC.", "Maros DC"),
    ]
    st_ba = resolve_overall_status("Batal", "RETUR", evs_balsis_active)
    assert st_ba == ST_RTS_BALSIS
    assert row_lifecycle({"status": st_ba, "order_status": "Batal", "events_json": _json.dumps([e.__dict__ for e in evs_balsis_active])}) == CAT_ACTIVE

    # 4. SPX F650 temporary redelivery postponement (dumyya pattern) must NOT be ST_FAILED / Gagal - Balsis or ST_OUT
    payload_delay = {"retcode": 0, "data": {"sls_tracking_info": {"records": [
        {"display_flag": 1, "actual_time": 1764145080, "tracking_code": "F650", "milestone_code": 6,
         "milestone_name": "Out for delivery", "tracking_name": "Delivery Attempt Failed",
         "buyer_description": "Pengiriman ditunda karena Pembeli tidak ditempat / tidak dapat dihubungi. Pesanan akan dikirim ulang.",
         "current_location": {"location_name": "Pabean Cantikan Hub"}},
        {"display_flag": 1, "actual_time": 1764137340, "tracking_code": "F600", "milestone_code": 6,
         "milestone_name": "Out for delivery", "tracking_name": "Out For Delivery",
         "buyer_description": "Pesanan dalam proses pengantaran.",
         "current_location": {"location_name": "Pabean Cantikan Hub"}},
    ]}}}
    with mock.patch.object(couriers._session, "get", return_value=FakeResp(payload_delay)):
        tr_delay = couriers.track_spx("SPXID_DELAY")
    assert tr_delay.status == ST_TRANSIT
    resolved_delay = resolve_overall_status("Sedang Dikirim", tr_delay.status, tr_delay.events)
    assert resolved_delay == ST_TRANSIT

    # DB._migrate() heals pre-existing false-positive Gagal - Balsis rows with F650 redelivery events
    tmp_db = os.path.join(tempfile.mkdtemp(), "heal_rts.db")
    db = DB(tmp_db)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, status, order_status, events_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("SPXID_DELAY", "SPX", "SN_D1", ST_RTS_BALSIS, "Sedang Dikirim", _json.dumps([e.__dict__ for e in tr_delay.events])),
    )
    db.conn.commit()
    db._migrate()
    assert db.get("SPXID_DELAY")["status"] == ST_TRANSIT
    db.close()

    # 5. SPX Delivered wording ("Pesanan tiba di alamat tujuan. diterima di Depan pintu.")
    assert resolve_overall_status(
        "Telah Dikirim",
        ST_TRANSIT,
        [TrackEvent("2026-11-25 16:08", "Pesanan tiba di alamat tujuan. diterima di Depan pintu.", "")],
    ) == ST_DELIVERED


def test_real_shopee_excel_cancel_and_failed_delivery_reimport():
    from resitrack.db import DB
    from resitrack.models import (
        CAT_ACTIVE,
        CAT_FINISHED,
        ST_CANCELED_BUYER,
        ST_CANCELED_SYSTEM,
        row_lifecycle,
    )

    p = os.path.join(tempfile.mkdtemp(), "Order.all.real.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "No. Pesanan", "Status Pesanan", "Alasan Pembatalan",
            "Status Pembatalan/ Pengembalian", "No. Resi", "Opsi Pengiriman", "Username (Pembeli)",
        ])
        w.writerow([
            "SN_SYS1", "Batal", "Dibatalkan secara otomatis oleh sistem Shopee. Alasan: Lainnya",
            "", "SPXID06328776617B", "SPX Hemat", "ngantoexx",
        ])
        w.writerow([
            "SN_BUY1", "Batal", "Dibatalkan oleh Pembeli. Alasan: Ubah Pesanan yang Ada",
            "", "", "SPX Hemat", "aambarrrr",
        ])
        w.writerow([
            "SN_FAIL1", "Batal", "Dibatalkan secara otomatis oleh sistem Shopee. Alasan: Pengiriman gagal",
            "", "SPXID06567450746B", "SPX Hemat", "farasiser",
        ])

    recs, _ = importer.parse_file(p)
    assert len(recs) == 3
    sys_rec = next(r for r in recs if r["order_sn"] == "SN_SYS1")
    buy_rec = next(r for r in recs if r["order_sn"] == "SN_BUY1")
    fail_rec = next(r for r in recs if r["order_sn"] == "SN_FAIL1")
    assert sys_rec["cancel_by"] == "system"
    assert buy_rec["cancel_by"] == "buyer"
    assert fail_rec["cancel_by"] == "system"

    tmp_db = os.path.join(tempfile.mkdtemp(), "reimport_heal.db")
    db = DB(tmp_db)
    # Seed legacy rows imported BEFORE cancel_by/cancel_reason existed (like user's current DB)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, status, order_status, events_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("SPXID06328776617B", "SPX", "SN_SYS1", "ngantoexx", ST_CANCELED_BUYER, "Batal",
         '[{"time":"2026-11-22 06:21","description":"Pengirim telah mengatur pengiriman. Menunggu pesanan diserahkan ke pihak jasa kirim.","location":""}]'),
    )
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, status, order_status) VALUES (?, ?, ?, ?, ?, ?)",
        ("ORDER-SN_BUY1", "SPX", "SN_BUY1", "aambarrrr", ST_CANCELED_BUYER, "Batal"),
    )
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, status, order_status) VALUES (?, ?, ?, ?, ?, ?)",
        ("SPXID06567450746B", "SPX", "SN_FAIL1", "farasiser", ST_CANCELED_BUYER, "Batal"),
    )
    db.conn.commit()

    for rec in recs:
        db.upsert(rec)

    r_sys = dict(db.get("SPXID06328776617B"))
    r_buy = dict(db.get("ORDER-SN_BUY1"))
    r_fail = dict(db.get("SPXID06567450746B"))

    assert r_sys["status"] == ST_CANCELED_SYSTEM and row_lifecycle(r_sys) == CAT_FINISHED
    assert r_buy["status"] == ST_CANCELED_BUYER and row_lifecycle(r_buy) == CAT_FINISHED
    # Untracked "Pengiriman gagal" transitions to ST_TRANSIT (Berjalan) so active_resis() tracks it!
    assert r_fail["status"] == ST_TRANSIT and row_lifecycle(r_fail) == CAT_ACTIVE
    assert "SPXID06567450746B" in {r["resi"] for r in db.active_resis()}
    db.close()


def test_payment_method_totals_fee_breakdown_and_inspector():
    import json
    from PySide6.QtWidgets import QApplication
    from resitrack.db import DB
    from resitrack.inspector import InspectorPanel
    from resitrack.models import (
        format_idr,
        is_cod_payment,
        parse_idr,
        payment_details_dict,
        payment_short_label,
    )

    assert parse_idr("100.500") == 100500
    assert parse_idr("7.288") == 7288
    assert parse_idr("1010") == 1010
    assert parse_idr("Rp 87.000") == 87000
    assert parse_idr("87000.00") == 87000
    assert parse_idr("N") == 0 and parse_idr("") == 0
    assert format_idr(469700) == "Rp469.700"
    assert is_cod_payment("COD (Bayar di Tempat)") and not is_cod_payment("QRIS")
    assert payment_short_label("COD (Bayar di Tempat)") == "COD"
    assert payment_short_label("QRIS") == "QRIS"
    assert payment_short_label("Saldo ShopeePay") == "ShopeePay"

    # Multi-item Shopee export: Subtotal Pesanan sums across rows; order-level fees/total do NOT sum
    p = os.path.join(tempfile.mkdtemp(), "pay_multi.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "No. Pesanan", "Status Pesanan", "No. Resi", "Username (Pembeli)",
            "Nama Produk", "Nama Variasi", "Metode Pembayaran", "Subtotal Pesanan",
            "Voucher Ditanggung Penjual", "Ongkos Kirim Dibayar oleh Pembeli",
            "Estimasi Potongan Biaya Pengiriman", "Total Pembayaran", "Perkiraan Ongkos Kirim",
        ])
        # Multi-item COD order (total=469700 = 4*124000 + 1700 - 28000)
        for v in ("Hitam", "Putih", "Navy", "Abu"):
            w.writerow([
                "261028S0CXKJK2", "Sedang Dikirim", "SPXID06017101360A", "sheilla.17",
                "Sarung Instan", v, "COD (Bayar di Tempat)", "124.000",
                "28.000", "1.700", "60.000", "469.700", "61.700",
            ])
        # Multi-item QRIS order with Rp1.000 service fee (total=45000 = 2*25000 - 6000 + 1000)
        for v in ("Koko A", "Koko B"):
            w.writerow([
                "261016R5VHFMRT", "Selesai", "SPXID06111222333A", "qris_buyer",
                "Sarung", v, "QRIS", "25.000", "6.000", "0", "22.000", "45.000", "22.000",
            ])

    recs, _ = importer.parse_file(p)
    assert len(recs) == 2

    cod_rec = next(r for r in recs if r["order_sn"] == "261028S0CXKJK2")
    assert cod_rec["payment_method"] == "COD (Bayar di Tempat)"
    assert cod_rec["total_payment"] == 469700
    cod_d = payment_details_dict(cod_rec)
    assert cod_d["product_subtotal"] == 496000
    assert cod_d["seller_voucher"] == 28000
    assert cod_d["buyer_shipping"] == 1700
    assert cod_d["shipping_discount"] == 60000
    assert cod_d["shipping_est"] == 61700
    assert "service_fee" not in cod_d

    qris_rec = next(r for r in recs if r["order_sn"] == "261016R5VHFMRT")
    assert qris_rec["payment_method"] == "QRIS"
    assert qris_rec["total_payment"] == 45000
    qris_d = payment_details_dict(qris_rec)
    assert qris_d["product_subtotal"] == 50000
    assert qris_d["seller_voucher"] == 6000
    assert qris_d["service_fee"] == 1000

    # Re-import enrichment populates payment fields on existing DB rows without duplicating
    tmp_db = os.path.join(tempfile.mkdtemp(), "pay_enrich.db")
    db = DB(tmp_db)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, order_status, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("SPXID06017101360A", "SPX", "261028S0CXKJK2", "sheilla.17", "Sedang Dikirim", ST_TRANSIT),
    )
    db.conn.commit()
    assert db.upsert(cod_rec) == "updated"
    assert len(db.all()) == 1
    row_cod = dict(db.get("SPXID06017101360A"))
    assert row_cod["payment_method"] == "COD (Bayar di Tempat)"
    assert row_cod["total_payment"] == 469700
    assert json.loads(row_cod["payment_details_json"])["product_subtotal"] == 496000

    # Manual row without payment data hides _payment_card; row with payment data shows it
    db.upsert({"resi": "SPXID_NO_PAY", "courier": "SPX", "order_sn": "SN_NOPAY", "order_status": "Sedang Dikirim"})
    row_nopay = dict(db.get("SPXID_NO_PAY"))

    _app = QApplication.instance() or QApplication([])
    insp = InspectorPanel()
    insp.set_rows([row_cod])
    assert not insp._payment_card.isHidden()
    assert insp._lbl_pay_total.text() == "Rp469.700"
    assert insp._pay_method_badge.text() == "COD"
    assert insp._pay_grid.rowCount() >= 3

    insp.set_rows([row_nopay])
    assert insp._payment_card.isHidden()
    insp.close()

    # ShopeeClient.sync preserves payment_method (including cod flag), total_amount, and details
    c = ShopeeClient({"shopee_partner_id": "1", "shopee_partner_key": "k", "shopee_refresh_token": "r", "shopee_shop_id": "2"})
    with mock.patch.object(c, "order_list", return_value=["SN_API_PAY"]), \
         mock.patch.object(c, "order_details", return_value=[{
             "order_sn": "SN_API_PAY",
             "order_status": "SHIPPED",
             "payment_method": "",
             "cod": True,
             "total_amount": 95000,
             "estimated_shipping_fee": 14000,
             "buyer_username": "api_buyer",
             "shipping_carrier": "SPX",
             "package_list": [{}],
             "item_list": [{"item_name": "Sarung", "model_name": "Hitam", "model_quantity_purchased": 1, "model_discounted_price": 95000}],
         }]), \
         mock.patch.object(c, "tracking_number", return_value="SPXID06999111000B"):
        synced = c.sync(7)
    assert synced[0]["payment_method"] == "COD (Bayar di Tempat)"
    assert synced[0]["total_payment"] == 95000
    assert payment_details_dict(synced[0])["product_subtotal"] == 95000
    db.close()


def test_menunggu_pickup_and_lainnya_category():
    import json
    from resitrack.db import DB
    from resitrack.models import CAT_ACTIVE, CAT_OTHER, ST_ERROR, effective_status, row_lifecycle

    # 1. Pre-pickup SPX F000 single event -> ST_PENDING (Menunggu Pickup), CAT_ACTIVE
    pre_ev = [
        TrackEvent("2026-11-26 10:00", "Pengirim telah mengatur pengiriman. Menunggu pesanan diserahkan ke pihak jasa kirim.", "")
    ]
    assert resolve_overall_status("Perlu Dikirim", ST_PENDING, pre_ev) == ST_PENDING
    assert resolve_overall_status("Sedang Dikirim", ST_PENDING, pre_ev) == ST_PENDING

    # 2. Unarranged Shopee order (ORDER-{sn}, empty events) -> ST_PENDING
    assert resolve_overall_status("Perlu Dikirim", "", []) == ST_PENDING
    assert resolve_overall_status("Sedang Dikemas", "", []) == ST_PENDING

    # 3. Handed over to courier -> ST_TRANSIT (Dalam Perjalanan)
    handed_evs = [
        TrackEvent("2026-11-26 14:00", "Pesanan telah diserahkan ke jasa kirim untuk diproses.", ""),
        TrackEvent("2026-11-26 10:00", "Pengirim telah mengatur pengiriman. Menunggu pesanan diserahkan ke pihak jasa kirim.", ""),
    ]
    assert resolve_overall_status("Sedang Dikirim", ST_TRANSIT, handed_evs) == ST_TRANSIT

    # 4. DB._migrate heals pre-existing ST_TRANSIT row with only pre-pickup event back to ST_PENDING
    tmp_db = os.path.join(tempfile.mkdtemp(), "pickup_heal.db")
    db = DB(tmp_db)
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, status, order_status, last_event, events_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "SPXID06700625619B", "SPX", "261126B01KKCNG", "sakinah21a", ST_TRANSIT, "Perlu Dikirim",
            pre_ev[0].description, json.dumps([e.__dict__ for e in pre_ev]),
        ),
    )
    db.conn.execute(
        "INSERT INTO shipments (resi, courier, order_sn, buyer_username, status, order_status, last_event, events_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "SPXID06662308453B", "SPX", "261125FARAS61", "faras61", ST_TRANSIT, "Sedang Dikirim",
            handed_evs[0].description, json.dumps([e.__dict__ for e in handed_evs]),
        ),
    )
    db.upsert({"resi": "SHPE_ERR_1", "courier": "POS", "order_sn": "SN_ERR", "order_status": "Sedang Dikirim"})
    db.save_tracking("SHPE_ERR_1", error="Gateway ZPI sedang gangguan sementara (HTTP 523).")
    db.conn.commit()
    db._migrate()

    r_pre = dict(db.get("SPXID06700625619B"))
    r_hand = dict(db.get("SPXID06662308453B"))
    r_err = dict(db.get("SHPE_ERR_1"))
    assert r_pre["status"] == ST_PENDING and row_lifecycle(r_pre) == CAT_ACTIVE
    assert r_hand["status"] == ST_TRANSIT and row_lifecycle(r_hand) == CAT_ACTIVE
    assert effective_status(r_err) == ST_ERROR and row_lifecycle(r_err) == CAT_OTHER

    # Tracking errors in CAT_OTHER stay eligible for retry via active_resis()
    assert "SHPE_ERR_1" in {r["resi"] for r in db.active_resis()}
    db.close()

    # 5. ShopeeClient.sync preserves READY_TO_SHIP without tracking number as ORDER-{sn}
    c = ShopeeClient({"shopee_partner_id": "1", "shopee_partner_key": "k", "shopee_refresh_token": "r", "shopee_shop_id": "2"})
    with mock.patch.object(c, "order_list", return_value=["SN_WAIT"]), \
         mock.patch.object(c, "order_details", return_value=[{
             "order_sn": "SN_WAIT",
             "order_status": "READY_TO_SHIP",
             "buyer_username": "wait_buyer",
             "shipping_carrier": "SPX",
             "package_list": [{}],
             "item_list": [{"item_name": "Sarung", "model_name": "Hitam", "model_quantity_purchased": 1}],
         }]), \
         mock.patch.object(c, "tracking_number", return_value=""):
        synced = c.sync(7)
    assert len(synced) == 1 and synced[0]["resi"] == "ORDER-SN_WAIT"


def test_duplicate_recipient_detection_and_virtual_copy_view():
    from PySide6.QtWidgets import QApplication
    import resitrack.config as cfg
    import resitrack.db as db_mod
    import resitrack.main_window as mw_mod
    from resitrack.models import (
        CAT_ACTIVE,
        CAT_FINISHED,
        CAT_OTHER,
        FILTER_DUPLICATE,
        duplicate_resi_map,
        recipient_dup_key,
    )

    # 1. Masked PII requires same city (no cross-city collision), unmasked matches normalized +62/0 phone
    r1 = {"resi": "R1", "recipient_name": "S******t", "recipient_phone": "******51", "city": "KAB. BERAU, KALIMANTAN TIMUR"}
    r2 = {"resi": "R2", "recipient_name": "s******t", "recipient_phone": "******51", "city": "Kab. Berau, Kalimantan Timur"}
    r_other_city = {"resi": "R3", "recipient_name": "S******t", "recipient_phone": "******51", "city": "KOTA BOGOR, JAWA BARAT"}
    dmap = duplicate_resi_map([r1, r2, r_other_city])
    assert set(dmap.keys()) == {"R1", "R2"}
    assert "R3" not in dmap

    u1 = {"resi": "U1", "recipient_name": "Budi Santoso", "recipient_phone": "0812-3456-7890", "city": "A"}
    u2 = {"resi": "U2", "recipient_name": "budi santoso", "recipient_phone": "+6281234567890", "city": "B"}
    assert recipient_dup_key(u1) == recipient_dup_key(u2)
    assert set(duplicate_resi_map([u1, u2]).keys()) == {"U1", "U2"}

    # 2. MainWindow UI: duplicates copied to LAINNYA -> Duplikat while staying in Berjalan & Selesai
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "dup_ui.db"
    old_cfg_db, old_cfg_set = cfg.DB_PATH, cfg.SETTINGS_PATH
    old_load, old_save = mw_mod.load_settings, mw_mod.save_settings
    try:
        cfg.DB_PATH = db_path
        cfg.SETTINGS_PATH = tmp / "settings.json"
        mw_mod.load_settings = lambda: dict(cfg.DEFAULTS)
        mw_mod.save_settings = lambda s: None

        db = db_mod.DB(db_path)
        db.upsert({
            "resi": "SPX_D1", "courier": "SPX", "order_sn": "SN_D1",
            "buyer_username": "buyer_new", "recipient_name": "S******t", "recipient_phone": "******51",
            "city": "KAB. BERAU", "order_status": "Sedang Dikirim",
        })
        db.upsert({
            "resi": "SPX_D2", "courier": "SPX", "order_sn": "SN_D2",
            "buyer_username": "buyer_old", "recipient_name": "S******t", "recipient_phone": "******51",
            "city": "KAB. BERAU", "order_status": "Selesai",
        })
        db.upsert({
            "resi": "SPX_UNIQ", "courier": "SPX", "order_sn": "SN_UNIQ",
            "buyer_username": "unique_buyer", "recipient_name": "A****i", "recipient_phone": "******99",
            "city": "KOTA BANDUNG", "order_status": "Sedang Dikirim",
        })
        db.upsert({
            "resi": "POS_ERR", "courier": "POS", "order_sn": "SN_ERR",
            "buyer_username": "err_buyer", "recipient_name": "E****n", "recipient_phone": "******11",
            "city": "KOTA JAKARTA", "order_status": "Sedang Dikirim",
        })
        db.save_tracking("POS_ERR", error="Gateway ZPI gangguan")
        db.close()

        app = QApplication.instance() or QApplication([])
        win = mw_mod.MainWindow(auto_sync=False)
        win.show()
        app.processEvents()

        # Counts MUST NOT remove duplicates from Berjalan or Selesai!
        assert win._seg_all.text() == "Semua · 4"
        assert win._seg_active.text() == "Berjalan · 2"
        assert win._seg_done.text() == "Selesai · 1"
        assert win._seg_other.text() == "Lainnya · 1"
        assert win._dup_nav_btn._cnt.text() == "2"

        # Original Berjalan & Selesai still contain their respective duplicate orders
        win._on_nav_clicked(CAT_ACTIVE, "")
        app.processEvents()
        assert win.proxy.rowCount() == 2

        win._on_nav_clicked(CAT_FINISHED, "")
        app.processEvents()
        assert win.proxy.rowCount() == 1

        # Duplikat virtual copy list shows both SPX_D1 & SPX_D2 together
        win._on_nav_clicked(CAT_OTHER, FILTER_DUPLICATE)
        app.processEvents()
        assert win.proxy.rowCount() == 2

        # Inspector shows _dup_card for duplicate and hides for unique
        win._select_and_focus_resi("SPX_D1")
        app.processEvents()
        assert not win.inspector._dup_card.isHidden()

        win._select_and_focus_resi("SPX_UNIQ")
        app.processEvents()
        assert win.inspector._dup_card.isHidden()
        win.close()
        app.processEvents()
    finally:
        cfg.DB_PATH, cfg.SETTINGS_PATH = old_cfg_db, old_cfg_set
        mw_mod.load_settings, mw_mod.save_settings = old_load, old_save


def test_workspace_checklist_filter_and_non_scroll_sidebar():
    from PySide6.QtWidgets import QApplication, QScrollArea
    import resitrack.config as cfg
    import resitrack.db as db_mod
    import resitrack.main_window as mw_mod
    from resitrack.models import (
        CAT_OTHER,
        FILTER_DUPLICATE,
        ST_CANCELED_BUYER,
        ST_CANCELED_SYSTEM,
        ST_COMPLETED,
        ST_RTS_BALSIS,
        TrackEvent,
    )

    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "chk_filter.db"
    old_cfg_db, old_cfg_set = cfg.DB_PATH, cfg.SETTINGS_PATH
    old_load, old_save = mw_mod.load_settings, mw_mod.save_settings
    try:
        cfg.DB_PATH = db_path
        cfg.SETTINGS_PATH = tmp / "settings.json"
        mw_mod.load_settings = lambda: dict(cfg.DEFAULTS)
        mw_mod.save_settings = lambda s: None

        db = db_mod.DB(db_path)
        # Group 1 (3 orders)
        db.upsert({
            "resi": "SPX_D_ACT", "courier": "SPX", "order_sn": "SN1", "buyer_username": "b1",
            "recipient_name": "S******t", "recipient_phone": "******51", "city": "BERAU",
            "order_status": "Sedang Dikirim", "payment_method": "COD (Bayar di Tempat)",
        })
        db.upsert({
            "resi": "SPX_D_CSYS", "courier": "SPX", "order_sn": "SN2", "buyer_username": "b2",
            "recipient_name": "S******t", "recipient_phone": "******51", "city": "BERAU",
            "order_status": "Batal", "cancel_by": "system", "payment_method": "COD (Bayar di Tempat)",
        })
        db.upsert({
            "resi": "SPX_D_CBUY", "courier": "SPX", "order_sn": "SN3", "buyer_username": "b3",
            "recipient_name": "S******t", "recipient_phone": "******51", "city": "BERAU",
            "order_status": "Batal", "cancel_by": "buyer", "payment_method": "QRIS",
        })
        # Group 2 (2 orders)
        db.upsert({
            "resi": "JNE_D_BAL", "courier": "JNE", "order_sn": "SN4", "buyer_username": "b4",
            "recipient_name": "A****i", "recipient_phone": "******22", "city": "BANDUNG",
            "order_status": "Batal", "status": "RTS", "payment_method": "QRIS",
        })
        db.upsert({
            "resi": "JNE_D_DONE", "courier": "JNE", "order_sn": "SN5", "buyer_username": "b5",
            "recipient_name": "A****i", "recipient_phone": "******22", "city": "BANDUNG",
            "order_status": "Selesai", "payment_method": "COD (Bayar di Tempat)",
        })
        # Unique order (1 order)
        db.upsert({
            "resi": "POS_UNIQ", "courier": "POS", "order_sn": "SN6", "buyer_username": "b6",
            "recipient_name": "U****q", "recipient_phone": "******99", "city": "SURABAYA",
            "order_status": "Sedang Dikirim", "payment_method": "Saldo ShopeePay",
        })
        db.close()

        app = QApplication.instance() or QApplication([])
        win = mw_mod.MainWindow(auto_sync=False)
        win.show()
        app.processEvents()

        # Non-scrolling rail & taller window
        rail = win.findChild(mw_mod.QFrame, "LeftRail")
        assert rail is not None and not rail.findChildren(QScrollArea)
        assert win.height() >= 740

        # Enter Duplikat view -> 5 duplicate orders
        win._on_nav_clicked(CAT_OTHER, FILTER_DUPLICATE)
        app.processEvents()
        assert win.proxy.rowCount() == 5

        # 1. Exclude Canceled (Pembeli & Sistem) inside Duplikat -> 3 non-canceled duplicate orders
        win._filter_popover._btn_no_canc.setChecked(True)
        win._filter_popover.apply_no_canceled_preset()
        app.processEvents()
        assert win.proxy.rowCount() == 3
        assert win.btn_filter.text() == "Filter · 1"
        assert win._filter_bar.isVisible()

        # 2. Also check Courier=JNE + Payment=QRIS -> only JNE_D_BAL
        win._filter_popover._courier_checks["JNE"].setChecked(True)
        win._filter_popover._pay_checks["QRIS"].setChecked(True)
        app.processEvents()
        assert win.proxy.rowCount() == 1
        assert win.proxy.index(0, 0).data(mw_mod.Qt.UserRole) == "JNE_D_BAL"
        assert win.btn_filter.text() == "Filter · 3"

        # 3. Reset all -> back to 5 in Duplikat and chip bar hidden
        win._filter_popover.reset_all()
        app.processEvents()
        assert win.proxy.rowCount() == 5
        assert win.btn_filter.text() == "Filter"
        assert win._filter_bar.isHidden()

        # 4. In Semua Pesanan: include Status=[Gagal - Balsis, Selesai] + Hanya Duplikat -> 2 orders
        win._on_nav_clicked("", "")
        win._filter_popover._st_checks[ST_RTS_BALSIS].setChecked(True)
        win._filter_popover._st_checks[ST_COMPLETED].setChecked(True)
        win._filter_popover._set_dup_mode("only")
        app.processEvents()
        assert win.proxy.rowCount() == 2

        # 5. Clear status + Non-Duplikat -> POS_UNIQ only
        win._filter_popover.clear_group("statuses")
        win._filter_popover._set_dup_mode("non_dup")
        app.processEvents()
        assert win.proxy.rowCount() == 1
        assert win.proxy.index(0, 0).data(mw_mod.Qt.UserRole) == "POS_UNIQ"

        win.close()
        app.processEvents()
    finally:
        cfg.DB_PATH, cfg.SETTINGS_PATH = old_cfg_db, old_cfg_set
        mw_mod.load_settings, mw_mod.save_settings = old_load, old_save


if __name__ == "__main__":
    import gc
    from PySide6.QtWidgets import QApplication
    from resitrack.icons import clear_cache

    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

    app = QApplication.instance()
    if app is not None:
        for w in list(app.topLevelWidgets()):
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
        app.processEvents()
    clear_cache()
    gc.collect()
