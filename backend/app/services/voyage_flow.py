"""航次登记回执、航线更正与货物挂接链路的领域实现。

业务约束：

* 航次登记先做船舶/航线/租船合约校验，全部通过后才落库；校验失败只留
  一条「已驳回」的登记凭据，绝不向航次列表写入残留记录。
* 每次成功登记生成一份「航次登记回执」，回执是货代挂货的唯一凭据；
  回执状态不是「有效」时，货物只能进入「舱位待确认」，不能真正挂接。
* 航线更正按「更正编号」幂等：同一更正重复送达（包括并发送达）只返回
  同一份有效航次编号，不会重复建航次。
* 挂接前「舱位待确认」的原货票不占用航次的已装载件数/吨位；挂接成功后
  原货票只保留一个正式航次编号。
* 被驳回的登记/更正不会出现在货代可选航次里。
"""
from __future__ import annotations

import threading
from typing import Any

from app.services.cross_module import (
    cargo_occupies_capacity,
    charter_active_for_vessel,
    check_cargo_tonnage,
    route_confirmed,
    vessel_confirmed,
)
from app.store import store

REGISTRATION_TABLE = "voyage_registration"
RECEIPT_TABLE = "voyage_receipt"
CORRECTION_TABLE = "voyage_correction"

REGISTERED = "已受理"
REG_PROCESSING = "处理中"
REG_REJECTED = "已驳回"

RECEIPT_PROCESSING = "处理中"
RECEIPT_VALID = "有效"
RECEIPT_REJECTED = "已驳回"

CORRECTION_PROCESSING = "处理中"
CORRECTION_APPLIED = "已生效"
CORRECTION_REJECTED = "已驳回"

PENDING_SPACE = "舱位待确认"
ATTACH_NONE = "未挂接"
ATTACH_PENDING = "待挂接"
ATTACH_DONE = "已挂接"
ATTACH_MERGED = "已合并"

VOYAGE_FIELDS = [
    "船舶编号", "航线编号", "计划离港", "计划到港", "实际离港", "实际到港", "航次状态", "备注说明",
]

# 并发送达同一更正时串行化；幂等判断在锁内完成，保证只生成一份航次/回执。
_FLOW_LOCK = threading.RLock()
_seed_receipts_ready = False


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _find_voyage(voyage_id: Any) -> dict[str, Any] | None:
    parsed = _parse_int(voyage_id)
    if parsed is None:
        # 兼容按「航次编号」字符串（如 VOY-0004）反查
        for row in store.rows("voyage"):
            if _norm(row.get("航次编号")) == _norm(voyage_id):
                return row
        return None
    return store.find("voyage", parsed)


def _find_receipt_by_no(receipt_no: Any) -> dict[str, Any] | None:
    target = _norm(receipt_no)
    if not target:
        return None
    for row in store.aux_rows(RECEIPT_TABLE):
        if _norm(row.get("回执编号")) == target:
            return row
    return None


def receipt_by_no(receipt_no: Any) -> dict[str, Any] | None:
    """按回执编号取登记回执（供货物登记校验调用）。"""
    ensure_receipts()
    if not _norm(receipt_no):
        return None
    return _find_receipt_by_no(receipt_no)


def receipt_for_voyage(voyage_id: Any) -> dict[str, Any] | None:
    """取某航次唯一的一份登记回执（优先有效回执）。"""
    target = _norm(voyage_id)
    if not target:
        return None
    candidates = [
        row for row in store.aux_rows(RECEIPT_TABLE)
        if _norm(row.get("航次编号")) == target
    ]
    candidates.sort(key=lambda row: (row.get("状态") == RECEIPT_VALID, row.get("id", 0)))
    for row in reversed(candidates):
        if row.get("状态") == RECEIPT_VALID:
            return row
    return candidates[-1] if candidates else None


def _find_registration_by_no(reg_no: Any) -> dict[str, Any] | None:
    target = _norm(reg_no)
    for row in store.aux_rows(REGISTRATION_TABLE):
        if _norm(row.get("登记编号")) == target:
            return row
    return None


def _find_correction(identifier: Any) -> dict[str, Any] | None:
    parsed = _parse_int(identifier)
    if parsed is not None:
        row = store.aux_find(CORRECTION_TABLE, parsed)
        if row is not None:
            return row
    target = _norm(identifier)
    for row in store.aux_rows(CORRECTION_TABLE):
        if _norm(row.get("更正编号")) == target:
            return row
    return None


def _receipt_state_for_voyage(voyage_id: Any) -> str:
    receipt = receipt_for_voyage(voyage_id)
    return _norm(receipt.get("状态")) if receipt else ""


# ---------------------------------------------------------------------------
# 历史航次回执补录
# ---------------------------------------------------------------------------
def ensure_receipts() -> None:
    """为已存在但还没有回执的航次补一份有效回执（仅执行一次）。

    种子航次在引入回执机制之前就已存在；补录后它们同样只能凭有效回执挂货。
    """
    global _seed_receipts_ready
    with _FLOW_LOCK:
        if _seed_receipts_ready:
            return
        receipts = store.aux_rows(RECEIPT_TABLE)
        for voyage in store.rows("voyage"):
            voyage_id = _norm(voyage.get("id"))
            if any(_norm(row.get("航次编号")) == voyage_id for row in receipts):
                continue
            entry = {
                "id": _next_id(receipts),
                "回执编号": f"VR-{_next_id(receipts):04d}",
                "航次编号": voyage_id,
                "登记编号": "",
                "货票编号": "",
                "更正编号": "",
                "船舶编号": voyage.get("船舶编号", ""),
                "航线编号": voyage.get("航线编号", ""),
                "计划离港": voyage.get("计划离港", ""),
                "状态": RECEIPT_VALID,
                "说明": "历史航次回执补录",
            }
            receipts.append(entry)
            voyage.setdefault("航次编号", voyage_id)
            voyage.setdefault("回执编号", entry["回执编号"])
            voyage.setdefault("登记状态", REGISTERED)
        _seed_receipts_ready = True


# ---------------------------------------------------------------------------
# 登记凭据
# ---------------------------------------------------------------------------
def _new_registration(values: dict[str, Any], *, status: str, reason: str = "",
                      source: str = "航次登记", correction_no: str = "",
                      receipt_no: str = "", voyage_id: Any = "") -> dict[str, Any]:
    rows = store.aux_rows(REGISTRATION_TABLE)
    entry = {
        "id": _next_id(rows),
        "登记编号": f"REG-{_next_id(rows):04d}",
        "状态": status,
        "来源": source,
        "船舶编号": values.get("船舶编号", ""),
        "航线编号": values.get("航线编号", ""),
        "计划离港": values.get("计划离港", ""),
        "货票编号": values.get("货票编号", ""),
        "更正编号": correction_no,
        "回执编号": receipt_no,
        "航次编号": _norm(voyage_id),
        "驳回原因": reason,
    }
    rows.append(entry)
    return entry


def _new_receipt(*, status: str, voyage_id: Any = "", registration_no: str = "",
                 correction_no: str = "", cargo_ticket: str = "",
                 values: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    rows = store.aux_rows(RECEIPT_TABLE)
    values = values or {}
    entry = {
        "id": _next_id(rows),
        "回执编号": f"VR-{_next_id(rows):04d}",
        "航次编号": _norm(voyage_id),
        "登记编号": registration_no,
        "货票编号": cargo_ticket,
        "更正编号": correction_no,
        "船舶编号": values.get("船舶编号", ""),
        "航线编号": values.get("航线编号", ""),
        "计划离港": values.get("计划离港", ""),
        "状态": status,
        "说明": note,
    }
    rows.append(entry)
    return entry


def _record_rejection(values: dict[str, Any], message: str, *, source: str,
                      correction_no: str = "") -> tuple[None, None, str]:
    """登记校验失败：只记驳回凭据，不写航次表。"""
    registration = _new_registration(
        values, status=REG_REJECTED, reason=message, source=source, correction_no=correction_no,
    )
    receipt = _new_receipt(
        status=RECEIPT_REJECTED, registration_no=registration["登记编号"],
        correction_no=correction_no, cargo_ticket=_norm(values.get("货票编号")),
        values=values, note=message,
    )
    registration["回执编号"] = receipt["回执编号"]
    return None, None, f"{message}（登记编号 {registration['登记编号']} 已驳回，未生成航次）"


def _validate_registration(values: dict[str, Any]) -> tuple[bool, str]:
    vessel_id = values.get("船舶编号", "")
    ok, msg = vessel_confirmed(vessel_id)
    if not ok:
        return False, f"创建航次失败：{msg}"
    ok, msg = route_confirmed(values.get("航线编号", ""))
    if not ok:
        return False, f"创建航次失败：{msg}"
    ok, msg = charter_active_for_vessel(vessel_id)
    if not ok:
        return False, f"创建航次失败：{msg}"
    return True, ""


def register_voyage(
    values: dict[str, Any],
    *,
    source: str = "航次登记",
    correction_no: str = "",
    receipt_status: str = RECEIPT_VALID,
    note: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """原子登记：校验通过才写航次并出回执；失败不留下任何航次记录。"""
    with _FLOW_LOCK:
        ensure_receipts()
        ok, message = _validate_registration(values)
        if not ok:
            return _record_rejection(values, message, source=source, correction_no=correction_no)

        rows = store.rows("voyage")
        entry: dict[str, Any] = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in VOYAGE_FIELDS})
        entry["status"] = "草稿"
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)

        registration = _new_registration(
            values, status=REGISTERED, source=source, correction_no=correction_no,
            voyage_id=entry["id"],
        )
        receipt = _new_receipt(
            status=receipt_status, voyage_id=entry["id"],
            registration_no=registration["登记编号"], correction_no=correction_no,
            cargo_ticket=_norm(values.get("货票编号")), values=values, note=note,
        )
        registration["回执编号"] = receipt["回执编号"]
        entry["航次编号"] = str(entry["id"])
        entry["回执编号"] = receipt["回执编号"]
        entry["登记编号"] = registration["登记编号"]
        entry["登记状态"] = REGISTERED
        entry["登记来源"] = source
        if correction_no:
            entry["更正编号"] = correction_no
        return entry, receipt, ""


def reject_registered_voyage(voyage_id: Any, reason: str = "") -> tuple[dict[str, Any] | None, str]:
    """驳回一笔已登记的航次：回执转驳回并从可选航次/航次列表移除。"""
    with _FLOW_LOCK:
        ensure_receipts()
        voyage = _find_voyage(voyage_id)
        if voyage is None:
            return None, f"航次 {voyage_id} 不存在或已归档"
        attached = [
            row for row in store.rows("cargo")
            if _norm(row.get("装载航次")) == _norm(voyage.get("id"))
            and row.get("挂接状态") == ATTACH_DONE
        ]
        if attached:
            return None, f"航次 {voyage.get('id')} 已有货物挂接，不能驳回登记"
        receipt = receipt_for_voyage(voyage.get("id"))
        if receipt is not None:
            receipt["状态"] = RECEIPT_REJECTED
            receipt["说明"] = reason or "登记被驳回"
        registration_no = _norm(voyage.get("登记编号"))
        registration = _find_registration_by_no(registration_no)
        if registration is not None:
            registration["状态"] = REG_REJECTED
            registration["驳回原因"] = reason or "登记被驳回"
        store.rows("voyage").remove(voyage)
        return voyage, f"航次 {voyage.get('id')} 登记已驳回，不再出现在可选航次中"


# ---------------------------------------------------------------------------
# 航线更正
# ---------------------------------------------------------------------------
def create_correction(values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """货代从原货票发起航线更正/待挂申请。此时航线可以暂时无效。"""
    with _FLOW_LOCK:
        ensure_receipts()
        correction_no = _norm(values.get("更正编号"))
        if correction_no:
            existing = _find_correction(correction_no)
            if existing is not None:
                return existing, f"更正 {correction_no} 已存在，沿用原记录"
        required = ["货票编号", "船舶编号", "航线编号", "计划离港"]
        missing = [field for field in required if not _norm(values.get(field))]
        if missing:
            return None, f"航线更正登记失败：缺少必填字段：{'、'.join(missing)}"

        rows = store.aux_rows(CORRECTION_TABLE)
        entry = {
            "id": _next_id(rows),
            "更正编号": correction_no or f"CORR-{_next_id(rows):04d}",
            "状态": CORRECTION_PROCESSING,
            "货票编号": _norm(values.get("货票编号")),
            "货物品名": values.get("货物品名", ""),
            "托运方": values.get("托运方", ""),
            "件数": values.get("件数", ""),
            "船舶编号": values.get("船舶编号", ""),
            "航线编号": values.get("航线编号", ""),
            "计划离港": values.get("计划离港", ""),
            "计划到港": values.get("计划到港", ""),
            "航次编号": "",
            "回执编号": "",
            "登记编号": "",
            "驳回原因": "",
            "送达次数": 0,
            "来源": "货代",
        }
        rows.append(entry)
        return entry, f"航线更正 {entry['更正编号']} 已受理，登记回执尚未返回"


def apply_correction(identifier: Any, values: dict[str, Any] | None = None, *,
                     hold: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """调度送达航线更正。

    按更正编号幂等：重复送达/并发送达返回同一航次编号与回执编号。
    ``hold=True`` 时先建出航次但把回执留在「处理中」，用于复现第一次响应
    还在途中、回执尚未返回的窗口。
    """
    with _FLOW_LOCK:
        ensure_receipts()
        correction = _find_correction(identifier)
        if correction is None:
            return None, None, f"更正记录 {identifier} 不存在"
        values = values or {}

        route_value = values.get("航线编号", values.get("route_id"))
        if route_value not in (None, ""):
            correction["航线编号"] = route_value
        for field in ("船舶编号", "计划离港", "计划到港", "货票编号", "件数"):
            if values.get(field) not in (None, ""):
                correction[field] = values.get(field)

        status = _norm(correction.get("状态"))
        if status == CORRECTION_REJECTED:
            return None, None, f"更正 {correction.get('更正编号')} 已被驳回，不能重新送达"

        # 1) 已生效：重复送达只返回原编号
        if status == CORRECTION_APPLIED:
            voyage = _find_voyage(correction.get("航次编号"))
            receipt = receipt_for_voyage(correction.get("航次编号"))
            if voyage is not None and receipt is not None and receipt.get("状态") == RECEIPT_VALID:
                return correction, receipt, (
                    f"更正已生效，沿用航次编号 {voyage['id']}、回执 {receipt['回执编号']}，不重复生成"
                )

        # 2) 已有航次（上一次送达已建航次但回执仍在途中）
        existing_voyage = _find_voyage(correction.get("航次编号"))
        if existing_voyage is not None:
            receipt = receipt_for_voyage(existing_voyage["id"])
            if receipt is None:
                receipt = _new_receipt(
                    status=RECEIPT_PROCESSING, voyage_id=existing_voyage["id"],
                    correction_no=_norm(correction.get("更正编号")),
                    cargo_ticket=_norm(correction.get("货票编号")), values=correction,
                )
                correction["回执编号"] = receipt["回执编号"]
            if hold and receipt.get("状态") != RECEIPT_VALID:
                receipt["状态"] = RECEIPT_PROCESSING
                correction["状态"] = CORRECTION_PROCESSING
                existing_voyage["登记状态"] = REG_PROCESSING
                correction["送达次数"] = int(correction.get("送达次数", 0)) + 1
                return correction, receipt, (
                    f"航次 {existing_voyage['id']} 的登记回执仍在处理中，回执编号 {receipt['回执编号']}"
                )
            receipt["状态"] = RECEIPT_VALID
            existing_voyage["登记状态"] = REGISTERED
            existing_voyage["回执编号"] = receipt["回执编号"]
            correction["状态"] = CORRECTION_APPLIED
            correction["航次编号"] = _norm(existing_voyage["id"])
            correction["回执编号"] = receipt["回执编号"]
            correction["送达次数"] = int(correction.get("送达次数", 0)) + 1
            return correction, receipt, (
                f"更正已生效，航次编号 {existing_voyage['id']}，回执 {receipt['回执编号']}"
            )

        # 3) 首次送达：校验船舶/航线/合约后建航次
        correction["送达次数"] = int(correction.get("送达次数", 0)) + 1
        ok, message = _validate_registration(correction)
        if not ok:
            correction["状态"] = CORRECTION_REJECTED
            correction["驳回原因"] = message
            registration, receipt, rejection_message = _record_rejection(
                correction, message, source="航线更正",
                correction_no=_norm(correction.get("更正编号")),
            )
            if receipt is not None:
                correction["回执编号"] = receipt["回执编号"]
                correction["登记编号"] = registration["登记编号"]
            return correction, receipt, f"更正处理失败：{message}"

        receipt_status = RECEIPT_PROCESSING if hold else RECEIPT_VALID
        voyage, receipt, _ = register_voyage(
            correction, source="航线更正", correction_no=_norm(correction.get("更正编号")),
            receipt_status=receipt_status,
            note="第一次响应在途，回执待返回" if hold else "",
        )
        if voyage is None or receipt is None:
            correction["状态"] = CORRECTION_REJECTED
            correction["驳回原因"] = "航次登记被驳回"
            correction["回执编号"] = _norm(receipt.get("回执编号")) if receipt else ""
            return correction, receipt, "更正处理失败：航次登记被驳回"

        correction["航次编号"] = _norm(voyage["id"])
        correction["回执编号"] = receipt["回执编号"]
        correction["登记编号"] = voyage.get("登记编号", "")
        if hold:
            correction["状态"] = CORRECTION_PROCESSING
            return correction, receipt, (
                f"航次 {voyage['id']} 已登记，回执 {receipt['回执编号']} 仍在处理中"
            )
        correction["状态"] = CORRECTION_APPLIED
        return correction, receipt, (
            f"更正已生效，航次编号 {voyage['id']}，回执 {receipt['回执编号']}"
        )


def reject_correction(identifier: Any, reason: str = "") -> tuple[dict[str, Any] | None, str]:
    with _FLOW_LOCK:
        ensure_receipts()
        correction = _find_correction(identifier)
        if correction is None:
            return None, f"更正记录 {identifier} 不存在"
        correction["状态"] = CORRECTION_REJECTED
        correction["驳回原因"] = reason or "调度驳回"
        receipt = _find_receipt_by_no(correction.get("回执编号"))
        if receipt is not None:
            receipt["状态"] = RECEIPT_REJECTED
            receipt["说明"] = reason or "调度驳回"
        voyage = _find_voyage(correction.get("航次编号"))
        if voyage is not None:
            attached = [
                row for row in store.rows("cargo")
                if _norm(row.get("装载航次")) == _norm(voyage.get("id"))
                and row.get("挂接状态") == ATTACH_DONE
            ]
            if not attached:
                store.rows("voyage").remove(voyage)
            else:
                voyage["登记状态"] = REG_REJECTED
        return correction, f"更正 {correction.get('更正编号')} 已驳回，对应航次不再可选"


# ---------------------------------------------------------------------------
# 货代可选航次与舱位待确认
# ---------------------------------------------------------------------------
def selectable_voyages() -> list[dict[str, Any]]:
    """货代可选的正式航次：必须有有效登记回执，且在途/驳回/已完成的一律排除。"""
    ensure_receipts()
    items: list[dict[str, Any]] = []
    for voyage in store.rows("voyage"):
        if voyage.get("status") in ("已完成",):
            continue
        if _norm(voyage.get("登记状态")) == REG_REJECTED:
            continue
        receipt = receipt_for_voyage(voyage.get("id"))
        if receipt is None or receipt.get("状态") != RECEIPT_VALID:
            continue
        attached_pieces = 0
        for cargo in store.rows("cargo"):
            if _norm(cargo.get("装载航次")) != _norm(voyage.get("id")):
                continue
            if not cargo_occupies_capacity(cargo):
                continue
            try:
                attached_pieces += int(float(str(cargo.get("件数", 0))))
            except (ValueError, TypeError):
                pass
        items.append({
            **voyage,
            "航次编号": _norm(voyage.get("航次编号")) or _norm(voyage.get("id")),
            "voyage_id": voyage.get("id"),
            "回执编号": receipt.get("回执编号"),
            "receipt_no": receipt.get("回执编号"),
            "回执状态": receipt.get("状态"),
            "已挂接件数": attached_pieces,
        })
    return items


def _correction_progress(correction: dict[str, Any]) -> dict[str, Any]:
    status = _norm(correction.get("状态"))
    receipt = _find_receipt_by_no(correction.get("回执编号"))
    if status == CORRECTION_REJECTED:
        return {
            "可挂接": False, "attachable": False,
            "办理进度": f"更正已驳回：{correction.get('驳回原因') or '未说明'}",
            "不可挂接原因": correction.get("驳回原因") or "更正已驳回",
            "回执状态": receipt.get("状态") if receipt else "已驳回",
        }
    if receipt is not None and receipt.get("状态") == RECEIPT_REJECTED:
        return {
            "可挂接": False, "attachable": False,
            "办理进度": "航次登记被驳回，原货票不能挂接",
            "不可挂接原因": "航次登记被驳回",
            "回执状态": RECEIPT_REJECTED,
        }
    if receipt is not None and receipt.get("状态") == RECEIPT_VALID:
        return {
            "可挂接": True, "attachable": True,
            "办理进度": f"航次登记回执已返回，可挂接航次 {correction.get('航次编号')}",
            "不可挂接原因": "",
            "回执状态": RECEIPT_VALID,
        }
    if receipt is not None and receipt.get("状态") == RECEIPT_PROCESSING:
        return {
            "可挂接": False, "attachable": False,
            "办理进度": f"航次 {correction.get('航次编号')} 已登记，登记回执 {receipt.get('回执编号')} 处理中",
            "不可挂接原因": "航次登记回执尚未返回",
            "回执状态": RECEIPT_PROCESSING,
        }
    if status == CORRECTION_APPLIED:
        return {
            "可挂接": False, "attachable": False,
            "办理进度": "更正已生效，但未找到有效登记回执",
            "不可挂接原因": "缺少有效航次登记回执",
            "回执状态": "",
        }
    return {
        "可挂接": False, "attachable": False,
        "办理进度": "航线更正处理中，航次登记回执尚未返回",
        "不可挂接原因": "航次登记回执尚未返回",
        "回执状态": receipt.get("状态") if receipt else "",
    }


def _cargo_progress(cargo: dict[str, Any]) -> dict[str, Any]:
    correction = _find_correction(cargo.get("更正编号")) if cargo.get("更正编号") else None
    receipt = None
    if correction is not None:
        progress = _correction_progress(correction)
        receipt = _find_receipt_by_no(correction.get("回执编号"))
        voyage_id = correction.get("航次编号", "")
        correction_no = correction.get("更正编号", "")
        correction_status = correction.get("状态", "")
    else:
        receipt = _find_receipt_by_no(cargo.get("回执编号"))
        voyage_id = cargo.get("装载航次", "")
        correction_no = cargo.get("更正编号", "")
        correction_status = ""
        if receipt is None:
            progress = {
                "可挂接": False, "attachable": False,
                "办理进度": "缺少航线更正记录或有效航次登记回执",
                "不可挂接原因": "缺少有效航次登记回执",
                "回执状态": "",
            }
        elif receipt.get("状态") == RECEIPT_VALID:
            progress = {
                "可挂接": True, "attachable": True,
                "办理进度": f"可挂接航次 {voyage_id}",
                "不可挂接原因": "", "回执状态": RECEIPT_VALID,
            }
        else:
            progress = {
                "可挂接": False, "attachable": False,
                "办理进度": f"登记回执{receipt.get('状态')}，暂不能挂接",
                "不可挂接原因": "航次登记回执不是有效状态",
                "回执状态": receipt.get("状态"),
            }
    result = {
        "货票编号": cargo.get("货票编号", ""),
        "ticket_no": cargo.get("货票编号", ""),
        "更正编号": correction_no,
        "correction_no": correction_no,
        "更正状态": correction_status,
        "回执编号": receipt.get("回执编号") if receipt else "",
        "receipt_no": receipt.get("回执编号") if receipt else "",
        "航次编号": _norm(voyage_id),
        "voyage_id": _parse_int(voyage_id),
        "件数": cargo.get("件数", ""),
        "pieces": cargo.get("件数", ""),
        "挂接状态": cargo.get("挂接状态", ATTACH_PENDING),
        "attachment": cargo.get("挂接状态", ATTACH_PENDING),
        "已挂接占用件数": 0,
    }
    result.update(progress)
    return result


def pending_spaces() -> list[dict[str, Any]]:
    """舱位待确认项：原货票 + 这一笔登记的办理进度；挂接前不占装载件数。"""
    items: list[dict[str, Any]] = []
    for cargo in store.rows("cargo"):
        attachment = _norm(cargo.get("挂接状态"))
        if cargo.get("status") != PENDING_SPACE and attachment not in (ATTACH_PENDING,):
            continue
        progress = _cargo_progress(cargo)
        items.append({**cargo, **progress, "原货票": cargo.get("货票编号", ""),
                      "原件数": cargo.get("件数", "")})
    return items


def ticket_progress(ticket_no: Any) -> dict[str, Any] | None:
    """按货票编号查询原货票及其登记的办理进度。"""
    target = _norm(ticket_no)
    if not target:
        return None
    rows = [row for row in store.rows("cargo") if _norm(row.get("货票编号")) == target]
    if not rows:
        return None
    cargo = rows[0]
    result = {
        "货票编号": target,
        "原货票": target,
        "ticket_no": target,
        "原件数": cargo.get("件数", ""),
        "pieces": cargo.get("件数", ""),
        "货物行数": len(rows),
        "完整货票": [dict(row) for row in rows],
    }
    result.update(_cargo_progress(cargo))
    return result


# ---------------------------------------------------------------------------
# 货物挂接
# ---------------------------------------------------------------------------
def _resolve_attach_target(cargo: dict[str, Any], receipt_no: Any = None,
                           voyage_id: Any = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """解析挂接目标：必须拿到有效航次登记回执。"""
    if receipt_no:
        receipt = _find_receipt_by_no(receipt_no)
        if receipt is None:
            return None, None, f"航次登记回执 {receipt_no} 不存在"
        if receipt.get("状态") != RECEIPT_VALID:
            return None, None, f"航次登记回执 {receipt_no} 状态为{receipt.get('状态')}，不是有效回执"
        voyage = _find_voyage(receipt.get("航次编号"))
        if voyage is None:
            return None, None, f"回执 {receipt_no} 对应的航次不存在"
        return voyage, receipt, ""

    if voyage_id not in (None, ""):
        voyage = _find_voyage(voyage_id)
        if voyage is None:
            return None, None, f"航次 {voyage_id} 不存在，不能挂接"
        receipt = receipt_for_voyage(voyage["id"])
        if receipt is None or receipt.get("状态") != RECEIPT_VALID:
            return None, None, f"航次 {voyage['id']} 没有有效航次登记回执，不能挂接"
        return voyage, receipt, ""

    correction = _find_correction(cargo.get("更正编号")) if cargo.get("更正编号") else None
    if correction is not None:
        progress = _correction_progress(correction)
        if not progress.get("可挂接"):
            return None, None, progress.get("不可挂接原因") or "登记回执尚未返回"
        receipt = _find_receipt_by_no(correction.get("回执编号"))
        voyage = _find_voyage(correction.get("航次编号"))
        if receipt is None or voyage is None or receipt.get("状态") != RECEIPT_VALID:
            return None, None, "更正对应的航次登记回执不是有效状态"
        return voyage, receipt, ""

    receipt = _find_receipt_by_no(cargo.get("回执编号"))
    if receipt is not None:
        if receipt.get("状态") != RECEIPT_VALID:
            return None, None, f"航次登记回执状态为{receipt.get('状态')}，不是有效回执"
        voyage = _find_voyage(receipt.get("航次编号"))
        if voyage is None:
            return None, None, "回执对应的航次不存在"
        return voyage, receipt, ""

    return None, None, "缺少有效航次登记回执，货物只能留在舱位待确认"


def attach_cargo(cargo_id: int, receipt_no: Any = None,
                 voyage_id: Any = None) -> tuple[dict[str, Any] | None, str]:
    """真正挂接：查到有效航次登记回执后才写入唯一正式航次编号。"""
    with _FLOW_LOCK:
        ensure_receipts()
        cargo = store.find("cargo", cargo_id)
        if cargo is None:
            return None, f"货物 {cargo_id} 不存在或已归档"

        if cargo.get("挂接状态") == ATTACH_DONE and _norm(cargo.get("装载航次")):
            return cargo, f"原货票 {cargo.get('货票编号') or cargo_id} 已挂接航次 {cargo.get('装载航次')}，不重复挂接"

        voyage, receipt, message = _resolve_attach_target(cargo, receipt_no=receipt_no, voyage_id=voyage_id)
        if voyage is None or receipt is None:
            return None, f"挂接失败：{message}"

        try:
            pieces = float(str(cargo.get("件数", 0)))
        except (ValueError, TypeError):
            pieces = 0.0
        tonnage_str = str(cargo.get("总吨数", "0"))
        try:
            tonnage = float(tonnage_str)
        except (ValueError, TypeError):
            tonnage = 0.0
        if tonnage > 0:
            ok, tonnage_msg = check_cargo_tonnage(voyage["id"], tonnage)
            if not ok:
                return None, f"挂接失败：{tonnage_msg}"

        cargo["装载航次"] = voyage["id"]
        cargo["航次编号"] = _norm(voyage.get("航次编号")) or _norm(voyage["id"])
        cargo["回执编号"] = receipt.get("回执编号")
        cargo["挂接状态"] = ATTACH_DONE
        cargo["status"] = "待装载"
        cargo["pending"] = True
        if cargo.get("更正编号"):
            cargo["办理进度"] = f"已挂接航次 {voyage['id']}（回执 {receipt.get('回执编号')}）"
        else:
            cargo["办理进度"] = f"已挂接航次 {voyage['id']}"

        # 同一原货票的其他待确认快照合并到这一笔正式航次，保证只有一个号码。
        ticket = _norm(cargo.get("货票编号"))
        if ticket:
            for other in store.rows("cargo"):
                if other is cargo:
                    continue
                if _norm(other.get("货票编号")) != ticket:
                    continue
                if other.get("挂接状态") == ATTACH_DONE:
                    continue
                other["挂接状态"] = ATTACH_MERGED
                other["装载航次"] = ""
                other["航次编号"] = ""
                other["status"] = ATTACH_MERGED
                other["pending"] = False
                other["办理进度"] = f"已合并到原货票 {ticket} 的唯一挂接记录"

        note = f"；原货票 {ticket or cargo_id} 件数 {cargo.get('件数', '')} 已挂接" if pieces >= 0 else ""
        return cargo, f"已挂接到航次 {voyage['id']}（有效回执 {receipt.get('回执编号')}）{note}"


# ---------------------------------------------------------------------------
# 货物登记辅助：把没有有效回执的提交收进舱位待确认
# ---------------------------------------------------------------------------
def find_cargo_by_ticket(ticket_no: Any) -> dict[str, Any] | None:
    target = _norm(ticket_no)
    if not target:
        return None
    for row in store.rows("cargo"):
        if _norm(row.get("货票编号")) == target:
            return row
    return None


def upsert_pending_cargo(values: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """无有效回执的货物提交落到舱位待确认，不产生第二个正式航次编号。"""
    ticket = _norm(values.get("货票编号"))
    if not ticket and values.get("更正编号"):
        correction = _find_correction(values.get("更正编号"))
        if correction is not None:
            ticket = _norm(correction.get("货票编号"))
            values = {**values, "货票编号": ticket}
    existing = find_cargo_by_ticket(ticket) if ticket else None
    if existing is not None and existing.get("挂接状态") == ATTACH_DONE:
        return existing, f"原货票 {ticket} 已挂接航次 {existing.get('装载航次')}，不重复登记"

    rows = store.rows("cargo")
    if existing is None:
        existing = {"id": _next_id(rows)}
        rows.append(existing)
        existing["status"] = PENDING_SPACE
        existing["pending"] = True
        existing["abnormal"] = False
    for field in ["货物品名", "托运方", "件数", "总吨数", "装货港", "卸货港", "备注说明"]:
        if values.get(field) not in (None, ""):
            existing[field] = values.get(field)
    existing["货票编号"] = ticket
    existing["挂接状态"] = ATTACH_PENDING
    existing["status"] = PENDING_SPACE
    existing["pending"] = True
    existing["装载航次"] = ""
    if values.get("更正编号"):
        existing["更正编号"] = values.get("更正编号")
    if values.get("回执编号"):
        existing["回执编号"] = values.get("回执编号")
    progress = _cargo_progress(existing)
    existing["办理进度"] = progress.get("办理进度", "")
    existing["可挂接"] = progress.get("可挂接", False)
    return existing, progress.get("办理进度", "")


def registration_records() -> list[dict[str, Any]]:
    ensure_receipts()
    return [dict(row) for row in store.aux_rows(REGISTRATION_TABLE)]


def receipt_records() -> list[dict[str, Any]]:
    ensure_receipts()
    return [dict(row) for row in store.aux_rows(RECEIPT_TABLE)]


def correction_records() -> list[dict[str, Any]]:
    ensure_receipts()
    return [dict(row) for row in store.aux_rows(CORRECTION_TABLE)]
