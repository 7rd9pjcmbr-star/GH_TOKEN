#!/usr/bin/env python3
"""Cấu trúc FROM đầy đủ — danh sách đơn hàng + giao hàng + kho/bưu cục.

From gốc: danh_sach_don_hang (7 field)
From đủ:  đơn · shop · kho · bưu cục · vận chuyển · người nhận/gửi · tiền · thời gian
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


# —— From gốc: danh sách đơn hàng ————————————————————


DANH_SACH_FIELDS: tuple[str, ...] = (
    "order_id",
    "platform",
    "customer_name",
    "total_price",
    "status",
    "order_created_at",
    "quantity",
)


class DanhSachDonHangRow(TypedDict):
    order_id: str
    platform: str
    customer_name: str | None
    total_price: float | int | None
    status: str
    order_created_at: str
    quantity: int | None


# —— Khối giao hàng (đầy đủ) ——————————————————————


class ReceiverInfo(TypedDict, total=False):
    """Thông tin người nhận / địa chỉ giao."""

    name: str | None
    phone: str | None
    province: str | None
    district: str | None
    ward: str | None
    address: str | None
    full_address: str | None
    postal_code: str | None
    country_code: str | None
    phone_class: str | None  # OK | masked | missing


class SenderInfo(TypedDict, total=False):
    """Thông tin người gửi / shop gửi hàng."""

    name: str | None
    phone: str | None
    province: str | None
    district: str | None
    ward: str | None
    address: str | None
    postal_code: str | None


class WarehouseInfo(TypedDict, total=False):
    """Kho xuất."""

    kho: str | None
    warehouse_id: str | None
    warehouse_display: str | None


class PostOfficeInfo(TypedDict, total=False):
    """Bưu cục / điểm vận chuyển."""

    buucuc: str | None
    backend: str | None
    carrier: str | None
    service_type: str | None


class ShippingInfo(TypedDict, total=False):
    """Thông tin giao hàng / vận chuyển đầy đủ."""

    tracking_code: str | None
    tracking_url: str | None
    tracking_provider: str | None
    tracking_ref: str | None
    status_shipping: str | None
    status_raw: str | None
    sub_status: str | None
    partner_id: str | None
    partner_name: str | None
    partner_fee: float | None
    shipping_fee: float | None
    estimated_shipping_fee: float | None
    actual_shipping_fee: float | None
    insurance_fee: float | None
    return_shipping_fee: float | None
    cod_service_fee: float | None
    estimate_delivery_date: str | None
    scheduled_pickup_at: str | None
    picked_at: str | None
    delivered_at: str | None
    time_send_partner: str | None
    last_update_status_at: str | None
    delivery_instruction: str | None
    delivery_failed_reason: str | None
    allow_partial_delivery: bool | None
    allow_try_on: bool | None
    allow_co_check: bool | None
    pickup_option: str | None
    delivery_attempts: int | None
    parcel_weight: float | None
    parcel_value: float | None
    service_type: str | None
    receiver: ReceiverInfo
    sender: SenderInfo
    warehouse: WarehouseInfo
    post_office: PostOfficeInfo


class MoneyInfo(TypedDict, total=False):
    total_price: float | None
    amount: float | None
    cod_amount: float | None
    cod_collected: bool | None
    cod_reconciled: bool | None
    cod_collection: bool | None  # SPX Y/N


class TimelineInfo(TypedDict, total=False):
    order_created_at: str | None
    created_at: str | None
    inserted_at: str | None
    synced_at: str | None
    updated_at: str | None
    event_at: str | None
    piped_at: str | None
    picked_at: str | None
    delivered_at: str | None


# —— From đầy đủ: OrderListItem ————————————————————


OrderStatus = Literal[
    "Moi tao",
    "Da xac nhan",
    "Dang giao",
    "Da gui hang",
    "Đã giao hàng",
    "unknown",
    "telegram_import",
]


class OrderListItem(TypedDict, total=False):
    """FROM đầy đủ: danh_sach + shop + kho + bưu cục + giao hàng."""

    # danh_sach (bắt buộc khi from danh_sach)
    order_id: str
    platform: str | None
    customer_name: str | None
    total_price: float | int | None
    status: str | None
    order_created_at: str | None
    quantity: int | None

    # định danh
    order_key: str | None
    van_tay: str | None
    so_noi_bo: str | None
    remote_id: str | None
    oms_id: str | None

    # shop
    shop_id: str | None
    shop_name: str | None
    page_id: str | None

    # trạng thái đơn
    status_order: str | None
    status_raw: str | None
    status_shipping: str | None

    # giao hàng (flat — tiện nginx/SQL) + khối lồng
    tracking_code: str | None
    tracking_url: str | None
    tracking_provider: str | None
    carrier: str | None
    buucuc: str | None
    backend: str | None
    kho: str | None
    warehouse_id: str | None
    warehouse_display: str | None
    customer_phone: str | None
    province: str | None
    district: str | None
    ward: str | None
    address: str | None
    full_address: str | None
    postal_code: str | None
    shipping_fee: float | None
    picked_at: str | None
    delivered_at: str | None
    shipping: ShippingInfo

    # tiền
    amount: float | None
    cod_amount: float | None
    money: MoneyInfo

    # người gửi (SPX / kho)
    sender_name: str | None
    sender_phone: str | None

    # nguồn / timeline
    source: str | None
    channel: str | None
    file: str | None
    origin: str | None
    created_at: str | None
    synced_at: str | None
    event_at: str | None
    piped_at: str | None
    timeline: TimelineInfo
    flow_path: str | None
    window_match: str | None
    window_dt: str | None


class OrderListBundle(TypedDict, total=False):
    ok: bool
    checked_at: str
    as_of: str
    window_start: str
    window_end: str
    count: int
    source: str
    by_status_order: dict[str, int]
    by_status_shipping: dict[str, int]
    by_shop: dict[str, int]
    by_kho: dict[str, int]
    by_buucuc: dict[str, int]
    by_platform: dict[str, int]
    orders: list[OrderListItem]


# —— Helpers ————————————————————————————————


def _s(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t if t and t.lower() not in {"none", "null"} else None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sync_flat_shipping(item: OrderListItem) -> OrderListItem:
    """Đồng bộ flat fields ↔ shipping{} để from luôn đủ giao hàng."""
    ship: ShippingInfo = dict(item.get("shipping") or {})
    recv: ReceiverInfo = dict(ship.get("receiver") or {})
    send: SenderInfo = dict(ship.get("sender") or {})
    wh: WarehouseInfo = dict(ship.get("warehouse") or {})
    po: PostOfficeInfo = dict(ship.get("post_office") or {})

    # flat → nested
    if item.get("tracking_code") and not ship.get("tracking_code"):
        ship["tracking_code"] = item.get("tracking_code")
    if item.get("tracking_url") and not ship.get("tracking_url"):
        ship["tracking_url"] = item.get("tracking_url")
    if item.get("tracking_provider") and not ship.get("tracking_provider"):
        ship["tracking_provider"] = item.get("tracking_provider")
    if item.get("status_shipping") and not ship.get("status_shipping"):
        ship["status_shipping"] = item.get("status_shipping")
    if item.get("status_raw") and not ship.get("status_raw"):
        ship["status_raw"] = item.get("status_raw")
    if item.get("carrier") and not ship.get("partner_name"):
        ship["partner_name"] = item.get("carrier")
    if item.get("shipping_fee") is not None and ship.get("shipping_fee") is None:
        ship["shipping_fee"] = item.get("shipping_fee")
    if item.get("picked_at") and not ship.get("picked_at"):
        ship["picked_at"] = item.get("picked_at")
    if item.get("delivered_at") and not ship.get("delivered_at"):
        ship["delivered_at"] = item.get("delivered_at")

    if item.get("customer_name") and not recv.get("name"):
        recv["name"] = item.get("customer_name")
    if item.get("customer_phone") and not recv.get("phone"):
        recv["phone"] = item.get("customer_phone")
    for fk, nk in (
        ("province", "province"),
        ("district", "district"),
        ("ward", "ward"),
        ("address", "address"),
        ("full_address", "full_address"),
        ("postal_code", "postal_code"),
    ):
        if item.get(fk) and not recv.get(nk):  # type: ignore[literal-required]
            recv[nk] = item.get(fk)  # type: ignore[literal-required]

    if item.get("sender_name") and not send.get("name"):
        send["name"] = item.get("sender_name")
    if item.get("sender_phone") and not send.get("phone"):
        send["phone"] = item.get("sender_phone")

    if item.get("kho") and not wh.get("kho"):
        wh["kho"] = item.get("kho")
    if item.get("warehouse_id") and not wh.get("warehouse_id"):
        wh["warehouse_id"] = item.get("warehouse_id")
    if item.get("warehouse_display") and not wh.get("warehouse_display"):
        wh["warehouse_display"] = item.get("warehouse_display")

    if item.get("buucuc") and not po.get("buucuc"):
        po["buucuc"] = item.get("buucuc")
    if item.get("backend") and not po.get("backend"):
        po["backend"] = item.get("backend")
    if item.get("carrier") and not po.get("carrier"):
        po["carrier"] = item.get("carrier")

    # nested → flat (đảm bảo from có giao hàng ở top-level)
    if ship.get("tracking_code") and not item.get("tracking_code"):
        item["tracking_code"] = ship.get("tracking_code")
    if ship.get("tracking_url") and not item.get("tracking_url"):
        item["tracking_url"] = ship.get("tracking_url")
    if ship.get("status_shipping") and not item.get("status_shipping"):
        item["status_shipping"] = ship.get("status_shipping")
    if ship.get("picked_at") and not item.get("picked_at"):
        item["picked_at"] = ship.get("picked_at")
    if ship.get("delivered_at") and not item.get("delivered_at"):
        item["delivered_at"] = ship.get("delivered_at")
    if recv.get("phone") and not item.get("customer_phone"):
        item["customer_phone"] = recv.get("phone")
    if recv.get("name") and not item.get("customer_name"):
        item["customer_name"] = recv.get("name")
    if recv.get("province") and not item.get("province"):
        item["province"] = recv.get("province")
    if recv.get("district") and not item.get("district"):
        item["district"] = recv.get("district")
    if recv.get("ward") and not item.get("ward"):
        item["ward"] = recv.get("ward")
    if (recv.get("full_address") or recv.get("address")) and not item.get("address"):
        item["address"] = recv.get("full_address") or recv.get("address")
    if recv.get("full_address") and not item.get("full_address"):
        item["full_address"] = recv.get("full_address")
    if wh.get("kho") and not item.get("kho"):
        item["kho"] = wh.get("kho")
    if wh.get("warehouse_id") and not item.get("warehouse_id"):
        item["warehouse_id"] = wh.get("warehouse_id")
    if po.get("buucuc") and not item.get("buucuc"):
        item["buucuc"] = po.get("buucuc")
    if po.get("carrier") and not item.get("carrier"):
        item["carrier"] = po.get("carrier")
    if po.get("backend") and not item.get("backend"):
        item["backend"] = po.get("backend")

    if not item.get("status_shipping") and item.get("status_order"):
        item["status_shipping"] = item.get("status_order")
        ship["status_shipping"] = item.get("status_order")
    if not item.get("status_order") and item.get("status"):
        item["status_order"] = item.get("status")

    ship["receiver"] = recv
    ship["sender"] = send
    ship["warehouse"] = wh
    ship["post_office"] = po
    item["shipping"] = ship

    money: MoneyInfo = dict(item.get("money") or {})
    if item.get("total_price") is not None and money.get("total_price") is None:
        money["total_price"] = _f(item.get("total_price"))
    if item.get("amount") is not None and money.get("amount") is None:
        money["amount"] = _f(item.get("amount"))
    if item.get("cod_amount") is not None and money.get("cod_amount") is None:
        money["cod_amount"] = _f(item.get("cod_amount"))
    item["money"] = money

    tl: TimelineInfo = dict(item.get("timeline") or {})
    for k in (
        "order_created_at",
        "created_at",
        "synced_at",
        "updated_at",
        "event_at",
        "piped_at",
        "picked_at",
        "delivered_at",
    ):
        if item.get(k) and not tl.get(k):  # type: ignore[literal-required]
            tl[k] = item.get(k)  # type: ignore[literal-required]
    item["timeline"] = tl
    return item


# —— FROM mappers ————————————————————————————


def from_danh_sach_row(row: dict[str, Any]) -> OrderListItem:
    """From danh_sach_don_hang — đủ khung giao hàng (rỗng nếu thiếu cột)."""
    status = _s(row.get("status"))
    item: OrderListItem = {
        "order_id": str(row.get("order_id") or ""),
        "platform": _s(row.get("platform")),
        "customer_name": _s(row.get("customer_name")),
        "total_price": _f(row.get("total_price")),
        "status": status,
        "order_created_at": _s(row.get("order_created_at")),
        "quantity": _i(row.get("quantity")),
        "status_order": status,
        "status_shipping": status,
        "origin": "danh_sach",
        "source": "danh_sach_don_hang",
        "channel": "xlsx",
        "shipping": {
            "status_shipping": status,
            "receiver": {"name": _s(row.get("customer_name"))},
            "sender": {},
            "warehouse": {},
            "post_office": {},
        },
        "money": {"total_price": _f(row.get("total_price"))},
        "timeline": {"order_created_at": _s(row.get("order_created_at"))},
    }
    return _sync_flat_shipping(item)


def from_detailed_csv_row(row: dict[str, Any]) -> OrderListItem:
    status = _s(row.get("status_normalized") or row.get("status"))
    item: OrderListItem = {
        "order_id": str(row.get("remote_id") or row.get("order_key") or row.get("id") or ""),
        "order_key": _s(row.get("order_key")),
        "remote_id": _s(row.get("remote_id")),
        "platform": _s(row.get("platform")),
        "shop_id": _s(row.get("shop_id")),
        "customer_name": _s(row.get("customer_name")),
        "customer_phone": _s(row.get("customer_phone")),
        "status": status,
        "status_order": status,
        "status_shipping": status,
        "status_raw": _s(row.get("status_raw")),
        "quantity": _i(row.get("quantity")),
        "amount": _f(row.get("amount")),
        "total_price": _f(row.get("amount") if row.get("amount") is not None else row.get("total_price")),
        "cod_amount": _f(row.get("cod_amount")),
        "order_created_at": _s(row.get("order_created_at")),
        "created_at": _s(row.get("order_created_at")),
        "synced_at": _s(row.get("synced_at")),
        "source": _s(row.get("source")),
        "channel": "csv",
        "file": _s(row.get("file")),
        "origin": "orders_detailed_csv",
        "shipping": {
            "status_shipping": status,
            "status_raw": _s(row.get("status_raw")),
            "receiver": {
                "name": _s(row.get("customer_name")),
                "phone": _s(row.get("customer_phone")),
            },
            "sender": {},
            "warehouse": {},
            "post_office": {},
        },
        "money": {
            "amount": _f(row.get("amount")),
            "total_price": _f(row.get("amount")),
            "cod_amount": _f(row.get("cod_amount")),
            "cod_collected": bool(row["cod_collected"]) if row.get("cod_collected") is not None else None,
            "cod_reconciled": bool(row["cod_reconciled"]) if row.get("cod_reconciled") is not None else None,
        },
    }
    return _sync_flat_shipping(item)


def from_kho_pipe_row(row: dict[str, Any]) -> OrderListItem:
    status = _s(row.get("status"))
    full_addr = _s(row.get("full_address") or row.get("address_detail"))
    item: OrderListItem = {
        "order_id": str(row.get("oms_id") or row.get("order_key") or row.get("so_noi_bo") or ""),
        "oms_id": _s(row.get("oms_id")),
        "order_key": _s(row.get("order_key")),
        "van_tay": _s(row.get("van_tay")),
        "so_noi_bo": _s(row.get("so_noi_bo")),
        "platform": _s(row.get("platform")),
        "shop_id": _s(row.get("shop_id")),
        "shop_name": _s(row.get("shop_name")),
        "customer_name": _s(row.get("receiver_name") or row.get("customer_name")),
        "customer_phone": _s(row.get("receiver_phone") or row.get("customer_phone")),
        "status": status,
        "status_order": status,
        "status_shipping": status,
        "kho": _s(row.get("kho")),
        "warehouse_id": _s(row.get("warehouse_id")),
        "warehouse_display": _s(row.get("warehouse_display")),
        "buucuc": _s(row.get("buucuc")),
        "backend": _s(row.get("backend")),
        "carrier": _s(row.get("carrier")),
        "tracking_code": _s(row.get("tracking_code")),
        "tracking_url": _s(row.get("tracking_url")),
        "tracking_provider": _s(row.get("tracking_provider")),
        "province": _s(row.get("province")),
        "district": _s(row.get("district")),
        "ward": _s(row.get("ward")),
        "address": full_addr,
        "full_address": full_addr,
        "postal_code": _s(row.get("postal_code")),
        "cod_amount": _f(row.get("cod_amount")),
        "sender_name": None,
        "picked_at": _s(row.get("picked_at")),
        "delivered_at": _s(row.get("delivered_at")),
        "created_at": _s(row.get("created_at")),
        "order_created_at": _s(row.get("created_at") or row.get("event_at")),
        "synced_at": _s(row.get("synced_at")),
        "event_at": _s(row.get("event_at")),
        "piped_at": _s(row.get("piped_at")),
        "source": _s(row.get("source")),
        "channel": _s(row.get("channel")),
        "file": _s(row.get("file")),
        "origin": "kho+buucuc",
        "flow_path": _s(row.get("flow_path")),
        "shipping": {
            "tracking_code": _s(row.get("tracking_code")),
            "tracking_url": _s(row.get("tracking_url")),
            "tracking_provider": _s(row.get("tracking_provider")),
            "tracking_ref": _s(row.get("tracking_ref")),
            "status_shipping": status,
            "picked_at": _s(row.get("picked_at")),
            "delivered_at": _s(row.get("delivered_at")),
            "partner_name": _s(row.get("carrier")),
            "receiver": {
                "name": _s(row.get("receiver_name")),
                "phone": _s(row.get("receiver_phone")),
                "province": _s(row.get("province")),
                "district": _s(row.get("district")),
                "ward": _s(row.get("ward")),
                "address": _s(row.get("address_detail")),
                "full_address": full_addr,
                "postal_code": _s(row.get("postal_code")),
                "phone_class": _s(row.get("phone_class")),
            },
            "sender": {
                "province": _s(row.get("sender_province")),
                "district": _s(row.get("sender_district")),
                "ward": _s(row.get("sender_ward")),
                "address": _s(row.get("sender_address")),
            },
            "warehouse": {
                "kho": _s(row.get("kho")),
                "warehouse_id": _s(row.get("warehouse_id")),
                "warehouse_display": _s(row.get("warehouse_display")),
            },
            "post_office": {
                "buucuc": _s(row.get("buucuc")),
                "backend": _s(row.get("backend")),
                "carrier": _s(row.get("carrier")),
            },
        },
        "money": {"cod_amount": _f(row.get("cod_amount"))},
    }
    return _sync_flat_shipping(item)


def from_spx_thanhcoong_row(row: dict[str, Any]) -> OrderListItem:
    """From thanhcoong.xlsx (SPX) — giao hàng đầy đủ."""
    track = _s(row.get("Tracking No.") or row.get("tracking_code"))
    status = _s(row.get("Tracking Status") or row.get("status_shipping"))
    item: OrderListItem = {
        "order_id": track or "",
        "platform": "SPX",
        "shop_id": _s(row.get("Account ID") or row.get("shop_id")),
        "customer_name": _s(row.get("Receiver Name")),
        "customer_phone": _s(row.get("Receiver Phone Number")),
        "status": status,
        "status_order": status,
        "status_shipping": status,
        "tracking_code": track,
        "tracking_url": _s(row.get("Tracking No. link")),
        "carrier": _s(row.get("3PL Name") or "SPX"),
        "province": _s(row.get("Receiver Province")),
        "district": _s(row.get("Receiver District(old)/Ward(new)")),
        "ward": _s(row.get("Receiver Ward(old)")),
        "address": _s(row.get("Receiver Detail Address")),
        "full_address": _s(row.get("Receiver Detail Address")),
        "postal_code": _s(row.get("Receiver Postal Code")),
        "sender_name": _s(row.get("Sender Name")),
        "sender_phone": _s(row.get("Sender Phone Number")),
        "kho": _s(row.get("Sender Name")),
        "order_created_at": _s(row.get("Create Time")),
        "created_at": _s(row.get("Create Time")),
        "picked_at": _s(row.get("Actual Pickup/Drop Off Time")),
        "delivered_at": _s(row.get("Delivered Time")),
        "cod_amount": _f(row.get("COD Amount")),
        "shipping_fee": _f(row.get("Actual Shipping Fee") or row.get("Estimated Shipping Fee")),
        "origin": "spx_thanhcoong",
        "source": "thanhcoong.xlsx",
        "channel": "xlsx",
        "shipping": {
            "tracking_code": track,
            "tracking_url": _s(row.get("Tracking No. link")),
            "tracking_provider": "SPX",
            "status_shipping": status,
            "partner_name": _s(row.get("3PL Name") or "SPX"),
            "service_type": _s(row.get("Service Type")),
            "scheduled_pickup_at": _s(row.get("Scheduled Pickup Time")),
            "picked_at": _s(row.get("Actual Pickup/Drop Off Time")),
            "delivered_at": _s(row.get("Delivered Time")),
            "delivery_instruction": _s(row.get("Delivery Instruction")),
            "delivery_failed_reason": _s(row.get("Delivery failed Reason")),
            "pickup_option": _s(row.get("Actual pickup option") or row.get("Original pickup option")),
            "delivery_attempts": _i(row.get("No of Delivery Attempts")),
            "parcel_weight": _f(row.get("Actual Weight") or row.get("Parcel Weight")),
            "parcel_value": _f(row.get("Parcel Value")),
            "estimated_shipping_fee": _f(row.get("Estimated Shipping Fee")),
            "actual_shipping_fee": _f(row.get("Actual Shipping Fee")),
            "shipping_fee": _f(row.get("Actual Shipping Fee")),
            "insurance_fee": _f(row.get("Insurance Fee")),
            "cod_service_fee": _f(row.get("COD Service Fee")),
            "return_shipping_fee": _f(row.get("Return Shipping Fee")),
            "receiver": {
                "name": _s(row.get("Receiver Name")),
                "phone": _s(row.get("Receiver Phone Number")),
                "province": _s(row.get("Receiver Province")),
                "district": _s(row.get("Receiver District(old)/Ward(new)")),
                "ward": _s(row.get("Receiver Ward(old)")),
                "address": _s(row.get("Receiver Detail Address")),
                "full_address": _s(row.get("Receiver Detail Address")),
                "postal_code": _s(row.get("Receiver Postal Code")),
            },
            "sender": {
                "name": _s(row.get("Sender Name")),
                "phone": _s(row.get("Sender Phone Number")),
                "province": _s(row.get("Sender Province")),
                "district": _s(row.get("Sender District(old)/Ward(new)")),
                "ward": _s(row.get("Sender Ward(old)")),
                "address": _s(row.get("Sender Detail Address")),
                "postal_code": _s(row.get("Sender Postal Code")),
            },
            "warehouse": {"kho": _s(row.get("Sender Name"))},
            "post_office": {
                "buucuc": "SPX",
                "carrier": _s(row.get("3PL Name") or "SPX"),
                "service_type": _s(row.get("Service Type")),
                "backend": "SPX",
            },
        },
        "money": {
            "cod_amount": _f(row.get("COD Amount")),
            "total_price": _f(row.get("Parcel Value")),
        },
    }
    cod_yn = row.get("COD Collection(Y/N)")
    if cod_yn is not None:
        money = dict(item.get("money") or {})
        money["cod_collection"] = str(cod_yn).strip().upper() in {"Y", "YES", "1", "TRUE"}
        item["money"] = money
    return _sync_flat_shipping(item)


def from_pancake_payload(order_wrap: dict[str, Any]) -> OrderListItem:
    """From orders_detailed_*.json item (có payload pancake) — đủ shipping_address."""
    pl = order_wrap.get("payload") if isinstance(order_wrap.get("payload"), dict) else order_wrap
    sa = pl.get("shipping_address") if isinstance(pl.get("shipping_address"), dict) else {}
    status_raw = _s(pl.get("status") or order_wrap.get("status_raw"))
    status = _s(order_wrap.get("status_normalized") or status_raw)
    recv_name = _s(sa.get("full_name") or order_wrap.get("customer_name") or pl.get("bill_full_name"))
    recv_phone = _s(sa.get("phone_number") or order_wrap.get("customer_phone") or pl.get("bill_phone_number"))
    full_addr = _s(sa.get("new_full_address") or sa.get("full_address") or sa.get("address"))
    item: OrderListItem = {
        "order_id": str(order_wrap.get("remote_id") or order_wrap.get("id") or pl.get("id") or ""),
        "order_key": _s(order_wrap.get("order_key")),
        "platform": _s(order_wrap.get("platform") or "Pancake/POS"),
        "shop_id": _s(order_wrap.get("shop_id") or pl.get("shop_id")),
        "page_id": _s(pl.get("page_id") or order_wrap.get("page_id")),
        "warehouse_id": _s(pl.get("warehouse_id") or order_wrap.get("warehouse_id")),
        "customer_name": recv_name,
        "customer_phone": recv_phone,
        "status": status,
        "status_order": status,
        "status_shipping": status,
        "status_raw": status_raw,
        "total_price": _f(pl.get("total_price") or order_wrap.get("amount")),
        "amount": _f(order_wrap.get("amount") or pl.get("total_price")),
        "cod_amount": _f(order_wrap.get("cod_amount") or pl.get("cod")),
        "shipping_fee": _f(pl.get("shipping_fee")),
        "quantity": _i(order_wrap.get("quantity") or pl.get("total_quantity")),
        "province": _s(sa.get("province_name")),
        "district": _s(sa.get("district_name")),
        "ward": _s(sa.get("commnue_name") or sa.get("commune_name")),
        "address": full_addr,
        "full_address": full_addr,
        "postal_code": _s(sa.get("post_code")),
        "order_created_at": _s(order_wrap.get("order_created_at") or pl.get("inserted_at")),
        "created_at": _s(pl.get("inserted_at") or order_wrap.get("order_created_at")),
        "synced_at": _s(order_wrap.get("synced_at")),
        "origin": "pancake_payload",
        "source": _s(order_wrap.get("source") or order_wrap.get("file")),
        "shipping": {
            "status_shipping": status,
            "status_raw": status_raw,
            "sub_status": _s(pl.get("sub_status")),
            "shipping_fee": _f(pl.get("shipping_fee")),
            "partner_fee": _f(pl.get("partner_fee")),
            "estimate_delivery_date": _s(pl.get("estimate_delivery_date")),
            "time_send_partner": _s(pl.get("time_send_partner")),
            "last_update_status_at": _s(pl.get("last_update_status_at")),
            "partner_id": _s(pl.get("shop_partner_id")),
            "receiver": {
                "name": recv_name,
                "phone": recv_phone,
                "province": _s(sa.get("province_name")),
                "district": _s(sa.get("district_name")),
                "ward": _s(sa.get("commnue_name")),
                "address": _s(sa.get("address")),
                "full_address": full_addr,
                "postal_code": _s(sa.get("post_code")),
                "country_code": _s(sa.get("country_code")),
            },
            "sender": {},
            "warehouse": {"warehouse_id": _s(pl.get("warehouse_id"))},
            "post_office": {},
        },
        "money": {
            "total_price": _f(pl.get("total_price")),
            "amount": _f(order_wrap.get("amount")),
            "cod_amount": _f(order_wrap.get("cod_amount") or pl.get("cod")),
            "cod_collected": bool(order_wrap["cod_collected"]) if order_wrap.get("cod_collected") is not None else None,
        },
    }
    return _sync_flat_shipping(item)


def merge_from(*parts: OrderListItem) -> OrderListItem:
    """Gộp nhiều from (danh_sach ← csv ← kho ← spx); field sau đè khi có giá trị."""
    out: dict[str, Any] = {}
    ships: list[dict[str, Any]] = []
    for p in parts:
        if not p:
            continue
        for k, v in p.items():
            if k == "shipping" and isinstance(v, dict):
                ships.append(v)
                continue
            if v is None or v == "" or v == {}:
                continue
            if k not in out or out[k] in (None, "", {}):
                out[k] = v
            elif isinstance(out[k], dict) and isinstance(v, dict):
                merged = dict(out[k])
                merged.update({kk: vv for kk, vv in v.items() if vv not in (None, "", {})})
                out[k] = merged
            else:
                out[k] = v
    ship: dict[str, Any] = {}
    for s in ships:
        for k, v in s.items():
            if isinstance(v, dict):
                ship.setdefault(k, {})
                if isinstance(ship[k], dict):
                    ship[k].update({kk: vv for kk, vv in v.items() if vv not in (None, "", {})})
            elif v not in (None, "", {}) and not ship.get(k):
                ship[k] = v
            elif v not in (None, "", {}):
                ship[k] = v
    if ship:
        out["shipping"] = ship
    return _sync_flat_shipping(out)  # type: ignore[arg-type]


def shipping_completeness(item: OrderListItem) -> dict[str, Any]:
    ship = item.get("shipping") or {}
    recv = ship.get("receiver") or {}
    need = {
        "tracking_code": bool(item.get("tracking_code") or ship.get("tracking_code")),
        "status_shipping": bool(item.get("status_shipping") or ship.get("status_shipping")),
        "receiver_phone": bool(item.get("customer_phone") or recv.get("phone")),
        "receiver_address": bool(
            item.get("address")
            or item.get("full_address")
            or recv.get("address")
            or recv.get("full_address")
            or item.get("province")
        ),
        "carrier_or_buucuc": bool(item.get("carrier") or item.get("buucuc") or ship.get("partner_name")),
        "kho_or_warehouse": bool(item.get("kho") or item.get("warehouse_id")),
    }
    ok_n = sum(1 for v in need.values() if v)
    return {"ok": ok_n == len(need), "score": f"{ok_n}/{len(need)}", "fields": need}


def describe_structure() -> dict[str, Any]:
    return {
        "title": "FROM đầy đủ — danh sách đơn + giao hàng",
        "base_danh_sach": list(DANH_SACH_FIELDS),
        "blocks": {
            "receiver": list(ReceiverInfo.__annotations__),
            "sender": list(SenderInfo.__annotations__),
            "warehouse": list(WarehouseInfo.__annotations__),
            "post_office": list(PostOfficeInfo.__annotations__),
            "shipping": [k for k in ShippingInfo.__annotations__ if k not in {"receiver", "sender", "warehouse", "post_office"}],
            "money": list(MoneyInfo.__annotations__),
            "timeline": list(TimelineInfo.__annotations__),
        },
        "from_mappers": [
            "from_danh_sach_row",
            "from_detailed_csv_row",
            "from_kho_pipe_row",
            "from_spx_thanhcoong_row",
            "from_pancake_payload",
            "merge_from",
        ],
        "shipping_in_from": True,
        "note": "Flat fields + shipping{} luôn đồng bộ qua _sync_flat_shipping",
    }


if __name__ == "__main__":
    import json
    from pathlib import Path

    schema = describe_structure()
    out = Path("reports/telegram-classify/order_list_structure.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(schema, ensure_ascii=False, indent=2))

    base = from_danh_sach_row(
        {
            "order_id": "SPXVN05958561510A",
            "platform": "POS",
            "customer_name": "Nguyen A",
            "total_price": 100000,
            "status": "Dang giao",
            "order_created_at": "2026-07-20 10:00:00",
            "quantity": 2,
        }
    )
    kho = from_kho_pipe_row(
        {
            "order_id": "SPXVN05958561510A",
            "tracking_code": "SPXVN05958561510A",
            "status_shipping": "Dang giao",
            "receiver_name": "Nguyen A",
            "receiver_phone": "0901234567",
            "province": "Ha Noi",
            "district": "Cau Giay",
            "ward": "Dich Vong",
            "address_detail": "12 Duy Tan",
            "kho": "Kho Thanh Cong",
            "warehouse_id": "f854a36f-9fb1-4089-abf8-8067216f1555",
            "buucuc": "SPX",
            "carrier": "SPX",
            "backend": "SPX",
            "cod_amount": 100000,
            "piped_at": "2026-07-20 12:00:00",
        }
    )
    spx = from_spx_thanhcoong_row(
        {
            "Tracking No.": "SPXVN05958561510A",
            "Tracking Status": "Delivering",
            "Receiver Name": "Nguyen A",
            "Receiver Phone Number": "0901234567",
            "Receiver Province": "Ha Noi",
            "Receiver Detail Address": "12 Duy Tan, Dich Vong, Cau Giay, Ha Noi",
            "Sender Name": "Shop Thanh Cong",
            "3PL Name": "SPX",
            "Actual Shipping Fee": 25000,
            "COD Amount": 100000,
            "COD Collection(Y/N)": "Y",
            "Create Time": "2026-07-20 09:00:00",
        }
    )
    full = merge_from(base, kho, spx)
    print("--- merge_from demo ---")
    print(json.dumps(full, ensure_ascii=False, indent=2))
    print("completeness", shipping_completeness(full))
    print("wrote", out)
