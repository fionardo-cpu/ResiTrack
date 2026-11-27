"""Shopee Open Platform v2 client (seller side).

Gives the link resi <-> buyer username:
  order.get_order_list  -> order_sn list
  order.get_order_detail(response_optional_fields=buyer_username,shipping_carrier,...) -> buyer_username
  logistics.get_tracking_number -> resi
  logistics.get_tracking_info   -> tracking history for any courier (SPX, JNE, POS, Anteraja, ...)

Requires a Shopee Open Platform app (partner_id + partner_key) authorised for your shop.
Docs: https://open.shopee.com/documents
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .couriers import detect_courier, normalize_resi
from .models import (ST_DELIVERED, ST_FAILED, ST_OUT, ST_PENDING, ST_RETURNED, ST_TRANSIT, TrackEvent,
                     TrackingError, TrackResult, infer_date_from_order_sn, is_masked_blank,
                     merge_products, parse_idr)

HOST_LIVE = "https://partner.shopeemobile.com"
HOST_TEST = "https://partner.test-stable.shopeemobile.com"

LOGISTICS_STATUS = {
    "LOGISTICS_NOT_START": ST_PENDING,
    "LOGISTICS_REQUEST_CREATED": ST_PENDING,
    "LOGISTICS_READY": ST_PENDING,
    "LOGISTICS_PICKUP_DONE": ST_TRANSIT,
    "LOGISTICS_PICKUP_RETRY": ST_PENDING,
    "LOGISTICS_DELIVERY_DONE": ST_DELIVERED,
    "LOGISTICS_DELIVERY_FAILED": ST_FAILED,
    "LOGISTICS_REQUEST_CANCELED": ST_PENDING,
    "LOGISTICS_COD_REJECTED": ST_RETURNED,
    "LOGISTICS_LOST": ST_FAILED,
    "LOGISTICS_PICKUP_FAILED": ST_FAILED,
    "LOGISTICS_INVALID": ST_FAILED,
}
TRACKABLE_ORDER_STATUS = {
    "READY_TO_SHIP",
    "PROCESSED",
    "SHIPPED",
    "TO_CONFIRM_RECEIVE",
    "COMPLETED",
    "RETRY_SHIP",
    "TO_RETURN",
    "CANCELLED",
    "IN_CANCEL",
}


class ShopeeError(TrackingError):
    pass


class ShopeeClient:
    def __init__(self, settings: dict, on_tokens_changed=None):
        self.s = settings
        self.on_tokens_changed = on_tokens_changed
        self.http = requests.Session()
        self._token_lock = threading.Lock()

    # ------------------------------------------------------------------ basics
    @property
    def host(self) -> str:
        return HOST_TEST if self.s.get("shopee_sandbox") else HOST_LIVE

    @property
    def partner_id(self) -> int:
        raw = str(self.s.get("shopee_partner_id") or "").strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError as e:
            raise ShopeeError("Partner ID harus berupa angka yang valid.") from e

    @property
    def shop_id(self) -> int:
        raw = str(self.s.get("shopee_shop_id") or "").strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError as e:
            raise ShopeeError("Shop ID harus berupa angka yang valid.") from e

    def configured(self) -> bool:
        return bool(self.s.get("shopee_partner_id") and self.s.get("shopee_partner_key"))

    def authorised(self) -> bool:
        return self.configured() and bool(self.s.get("shopee_refresh_token") and self.s.get("shopee_shop_id"))

    def _sign(self, base: str) -> str:
        key = str(self.s.get("shopee_partner_key", "")).encode()
        return hmac.new(key, base.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ auth
    def auth_url(self) -> str:
        path = "/api/v2/shop/auth_partner"
        ts = int(time.time())
        sign = self._sign(f"{self.partner_id}{path}{ts}")
        q = urlencode({"partner_id": self.partner_id, "timestamp": ts, "sign": sign,
                       "redirect": self.s.get("shopee_redirect_url") or "https://www.google.com"})
        return f"{self.host}{path}?{q}"

    @staticmethod
    def parse_redirect(url: str) -> tuple[str, str]:
        q = parse_qs(urlparse(url.strip()).query)
        code = (q.get("code") or [""])[0]
        shop_id = (q.get("shop_id") or [""])[0]
        if not code or not shop_id:
            raise ShopeeError("URL redirect tidak berisi 'code' dan 'shop_id'.")
        return code, shop_id

    def _public_post(self, path: str, body: dict) -> dict:
        ts = int(time.time())
        sign = self._sign(f"{self.partner_id}{path}{ts}")
        try:
            r = self.http.post(
                f"{self.host}{path}",
                params={"partner_id": self.partner_id, "timestamp": ts, "sign": sign},
                json=body,
                timeout=30,
            )
        except requests.RequestException as e:
            raise ShopeeError(f"Koneksi ke Shopee gagal: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise ShopeeError(f"Shopee HTTP {r.status_code}") from e
        if data.get("error"):
            raise ShopeeError(f"{data.get('error')}: {data.get('message')}")
        return data

    def _store_tokens(self, data: dict, shop_id) -> None:
        if not data.get("access_token") or not data.get("refresh_token"):
            raise ShopeeError("Respons Shopee tidak menyertakan token akses.")
        self.s["shopee_shop_id"] = str(shop_id)
        self.s["shopee_access_token"] = data["access_token"]
        self.s["shopee_refresh_token"] = data["refresh_token"]
        self.s["shopee_token_expire_at"] = int(time.time()) + int(data.get("expire_in", 14400)) - 300
        if self.on_tokens_changed:
            self.on_tokens_changed(self.s)

    def exchange_code(self, code: str, shop_id: str) -> None:
        try:
            sid_int = int(str(shop_id).strip())
        except ValueError as e:
            raise ShopeeError("Parameter shop_id pada URL redirect tidak valid.") from e
        data = self._public_post(
            "/api/v2/auth/token/get",
            {"code": code, "shop_id": sid_int, "partner_id": self.partner_id},
        )
        self._store_tokens(data, shop_id)

    def refresh(self) -> None:
        data = self._public_post(
            "/api/v2/auth/access_token/get",
            {
                "refresh_token": self.s["shopee_refresh_token"],
                "shop_id": self.shop_id,
                "partner_id": self.partner_id,
            },
        )
        self._store_tokens(data, self.shop_id)

    def ensure_token(self) -> None:
        with self._token_lock:
            if not self.authorised():
                raise ShopeeError("Shopee API belum diotorisasi (Pengaturan > Shopee API).")
            if time.time() >= float(self.s.get("shopee_token_expire_at") or 0):
                self.refresh()

    # ------------------------------------------------------------------ shop calls
    def get(self, path: str, params: dict, _retry: bool = True) -> dict:
        self.ensure_token()
        ts = int(time.time())
        token = self.s["shopee_access_token"]
        sign = self._sign(f"{self.partner_id}{path}{ts}{token}{self.shop_id}")
        common = {
            "partner_id": self.partner_id,
            "timestamp": ts,
            "sign": sign,
            "access_token": token,
            "shop_id": self.shop_id,
        }
        try:
            r = self.http.get(f"{self.host}{path}", params={**common, **params}, timeout=30)
        except requests.RequestException as e:
            raise ShopeeError(f"Koneksi ke Shopee gagal: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise ShopeeError(f"Shopee HTTP {r.status_code}") from e
        if data.get("error"):
            if _retry and "token" in str(data.get("error")).lower():
                self.s["shopee_token_expire_at"] = 0
                return self.get(path, params, _retry=False)
            raise ShopeeError(f"{data.get('error')}: {data.get('message')}")
        return data.get("response") or {}

    def order_list(self, days: int = 15, time_range_field: str = "create_time",
                   since_ts: int | None = None, until_ts: int | None = None) -> list[str]:
        now = int(time.time())
        end = min(int(until_ts), now) if until_ts is not None else now
        start = int(since_ts) if since_ts is not None else (end - days * 86400)
        if start >= end:
            start = max(0, end - 60)
        sns: list[str] = []
        seen: set[str] = set()
        win_from = start
        while win_from < end:                       # API allows max 15-day windows
            win_to = min(win_from + 15 * 86400 - 1, end)
            cursor = ""
            while True:
                resp = self.get("/api/v2/order/get_order_list", {
                    "time_range_field": time_range_field, "time_from": win_from, "time_to": win_to,
                    "page_size": 100, "cursor": cursor,
                    "response_optional_fields": "order_status"})
                for o in resp.get("order_list", []):
                    if o.get("order_status", "SHIPPED") in TRACKABLE_ORDER_STATUS:
                        sn = o.get("order_sn") or ""
                        if sn and sn not in seen:
                            seen.add(sn)
                            sns.append(sn)
                if not resp.get("more"):
                    break
                cursor = resp.get("next_cursor", "")
            win_from = win_to + 1
        return sns

    def order_details(self, order_sns: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(order_sns), 50):
            resp = self.get("/api/v2/order/get_order_detail", {
                "order_sn_list": ",".join(order_sns[i:i + 50]),
                "response_optional_fields": "buyer_username,shipping_carrier,recipient_address,item_list,"
                                            "package_list,note,order_status,cancel_by,cancel_reason,buyer_cancel_reason,"
                                            "payment_method,total_amount,cod,actual_shipping_fee,estimated_shipping_fee,"
                                            "message_to_seller,reverse_shipping_fee"})
            out.extend(resp.get("order_list", []))
        return out

    def escrow_detail(self, order_sn: str) -> dict:
        if not self.authorised() or not order_sn:
            return {}
        try:
            return self.get("/api/v2/payment/get_escrow_detail", {"order_sn": order_sn})
        except TrackingError:
            return {}

    def tracking_number(self, order_sn: str, package_number: str = "") -> str:
        p = {"order_sn": order_sn}
        if package_number:
            p["package_number"] = package_number
        return self.get("/api/v2/logistics/get_tracking_number", p).get("tracking_number", "")

    def tracking_info(self, order_sn: str, resi: str, courier: str, package_number: str = "") -> TrackResult:
        p = {"order_sn": order_sn}
        if package_number:
            p["package_number"] = package_number
        resp = self.get("/api/v2/logistics/get_tracking_info", p)
        res = TrackResult(resi=resi, courier=courier, source="Shopee Open API", raw=resp)
        for t in resp.get("tracking_info", []):
            try:
                ts = int(t.get("update_time") or 0)
            except (TypeError, ValueError):
                ts = 0
            when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts > 0 else ""
            res.events.append(TrackEvent(when, t.get("description", "")))
        res.events.sort(key=lambda e: e.time, reverse=True)
        res.status = LOGISTICS_STATUS.get(resp.get("logistics_status", ""), ST_TRANSIT)
        if res.events:
            res.last_event, res.last_time = res.events[0].description, res.events[0].time
            low = res.last_event.lower()
            if res.status == ST_TRANSIT and any(k in low for k in ("diantar", "dalam proses pengantaran", "out for delivery")):
                res.status = ST_OUT
        return res

    # ------------------------------------------------------------------ high level
    def sync(self, days: int = 15, progress=None, since_ts: int | None = None,
             until_ts: int | None = None, time_range_field: str = "create_time") -> list[dict]:
        """Fetch shipped/updated orders and return shipment records (resi + buyer username + escrow)."""
        sns = self.order_list(days=days, time_range_field=time_range_field, since_ts=since_ts, until_ts=until_ts)
        if not sns:
            return []
        if progress:
            progress(f"{len(sns)} pesanan ditemukan, mengambil detail...")
        orders = self.order_details(sns)

        escrow_map: dict[str, dict] = {}
        order_sns = [o["order_sn"] for o in orders if o.get("order_sn")]
        if order_sns and self.authorised():
            if progress:
                progress(f"Mengambil rincian biaya {len(order_sns)} pesanan...")
            if len(order_sns) == 1:
                escrow_map[order_sns[0]] = self.escrow_detail(order_sns[0])
            else:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    for sn_k, esc_val in zip(order_sns, ex.map(self.escrow_detail, order_sns)):
                        escrow_map[sn_k] = esc_val

        records: list[dict] = []
        for o in orders:
            sn = o["order_sn"]
            try:
                c_ts = int(o.get("create_time") or 0)
            except (TypeError, ValueError):
                c_ts = 0
            order_date = datetime.fromtimestamp(c_ts).strftime("%Y-%m-%d %H:%M") if c_ts > 0 else infer_date_from_order_sn(sn)
            ord_st = o.get("order_status", "")
            cancel_by = o.get("cancel_by", "") or ""
            cancel_reason = o.get("buyer_cancel_reason") or o.get("cancel_reason") or ""
            pm = (o.get("payment_method") or "").strip()
            if o.get("cod"):
                if not pm:
                    pm = "COD (Bayar di Tempat)"
                elif "cod" not in pm.lower():
                    pm = f"COD ({pm})"
            tp = parse_idr(o.get("total_amount"))
            carrier = o.get("shipping_carrier", "")
            packages = o.get("package_list") or [{}]
            addr = o.get("recipient_address") or {}
            rec_name = "" if is_masked_blank(addr.get("name", "")) else (addr.get("name") or "").strip()
            rec_phone = "" if is_masked_blank(addr.get("phone", "")) else (addr.get("phone") or "").strip()
            raw_city = (addr.get("city") or "").strip()
            raw_state = (addr.get("state") or "").strip()
            if is_masked_blank(raw_city):
                raw_city = ""
            if is_masked_blank(raw_state):
                raw_state = ""
            city_val = f"{raw_city}, {raw_state}" if (raw_city and raw_state and raw_state.lower() not in raw_city.lower()) else (raw_city or raw_state)

            items = ""
            item_sub = 0
            for it in o.get("item_list", []):
                name = (it.get("item_name") or "").strip()
                var = (it.get("model_name") or "").strip()
                qty = int(it.get("model_quantity_purchased") or 1)
                item_sub += parse_idr(it.get("model_discounted_price")) * qty
                if var:
                    entry = f"{name} ({var}{' x' + str(qty) if qty > 1 else ''})"
                else:
                    entry = f"{name}{' x' + str(qty) if qty > 1 else ''}"
                items = merge_products(items, entry)

            esc = escrow_map.get(sn) or {}
            bpi = esc.get("buyer_payment_info") or {}
            oi = esc.get("order_income") or {}
            if tp <= 0:
                tp = parse_idr(bpi.get("buyer_total_amount") or oi.get("buyer_total_amount"))

            ship_est = parse_idr(o.get("actual_shipping_fee") or o.get("estimated_shipping_fee") or oi.get("actual_shipping_fee") or oi.get("estimated_shipping_fee"))
            fee_map: dict[str, int] = {}
            sub_val = parse_idr(bpi.get("merchant_subtotal") or oi.get("order_discounted_price")) or item_sub
            if sub_val > 0:
                fee_map["product_subtotal"] = sub_val
            b_ship = parse_idr(bpi.get("shipping_fee"))
            if b_ship > 0:
                fee_map["buyer_shipping"] = b_ship
            if ship_est > 0:
                fee_map["shipping_est"] = ship_est
            s_disc = parse_idr(oi.get("shopee_shipping_rebate"))
            if s_disc > 0:
                fee_map["shipping_discount"] = s_disc
            s_vouch = abs(int(bpi.get("seller_voucher") or 0)) or parse_idr(oi.get("voucher_from_seller"))
            if s_vouch > 0:
                fee_map["seller_voucher"] = s_vouch
            sh_vouch = abs(int(bpi.get("shopee_voucher") or 0)) or parse_idr(oi.get("voucher_from_shopee"))
            if sh_vouch > 0:
                fee_map["shopee_voucher"] = sh_vouch
            coin_v = abs(int(bpi.get("shopee_coins_redeemed") or 0))
            if coin_v > 0:
                fee_map["coins_discount"] = coin_v
            cc_v = abs(int(bpi.get("credit_card_promotion") or 0))
            if cc_v > 0:
                fee_map["cc_discount"] = cc_v
            svc_v = parse_idr(bpi.get("buyer_service_fee") or oi.get("buyer_transaction_fee"))
            if svc_v > 0:
                fee_map["service_fee"] = svc_v
            ret_v = parse_idr(o.get("reverse_shipping_fee"))
            if ret_v > 0:
                fee_map["return_shipping"] = ret_v
            seller_fees = (
                parse_idr(oi.get("commission_fee"))
                + parse_idr(oi.get("service_fee"))
                + parse_idr(oi.get("seller_order_processing_fee"))
                + parse_idr(oi.get("withholding_tax"))
            )
            if seller_fees > 0:
                fee_map["seller_fees"] = seller_fees
            escrow_amt = parse_idr(oi.get("escrow_amount_after_adjustment") or oi.get("escrow_amount"))
            if escrow_amt > 0:
                fee_map["escrow_amount"] = escrow_amt

            pay_details = json.dumps(fee_map, ensure_ascii=False) if fee_map else "{}"
            user_note = (o.get("message_to_seller") or o.get("note") or "").strip()

            for pkg in packages:
                pn = pkg.get("package_number", "")
                try:
                    resi = normalize_resi(self.tracking_number(sn, pn))
                except ShopeeError:
                    resi = ""
                if not resi:
                    if ord_st in ("CANCELLED", "IN_CANCEL", "READY_TO_SHIP", "PROCESSED", "RETRY_SHIP"):
                        resi = f"ORDER-{sn}"
                    else:
                        continue
                pkg_note = user_note or (f"pkg:{pn}" if pn and len(packages) > 1 else "")
                records.append({
                    "resi": resi,
                    "courier": detect_courier(resi, pkg.get("shipping_carrier") or carrier),
                    "order_sn": sn,
                    "order_date": order_date,
                    "buyer_username": o.get("buyer_username", ""),
                    "recipient_name": rec_name,
                    "recipient_phone": rec_phone,
                    "city": city_val,
                    "product": items,
                    "order_source": "shopee_api",
                    "order_status": ord_st,
                    "cancel_by": cancel_by,
                    "cancel_reason": cancel_reason,
                    "payment_method": pm,
                    "total_payment": tp,
                    "payment_details_json": pay_details,
                    "note": pkg_note,
                })
        return records
