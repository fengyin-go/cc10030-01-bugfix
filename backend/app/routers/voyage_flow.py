"""航次登记回执 / 航线更正 / 舱位待确认 / 货物挂接 的流程接口。

通用资源路由（/api/{module}）之外，这里把跨模块的协作链路单独暴露出来：
- 调度登记航次后立刻拿到「航次登记回执」；
- 货代按回执编号或航次编号挂接原货票；
- 货代可选航次列表只包含有有效回执的航次；
- 舱位待确认项同时返回原货票与这一笔登记的办理进度；
- 航线更正按更正编号幂等，支持先在途、再返回有效回执。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.voyage_flow import (
    apply_correction,
    attach_cargo,
    create_correction,
    pending_spaces,
    receipt_by_no,
    reject_correction,
    reject_registered_voyage,
    registration_records,
    receipt_records,
    selectable_voyages,
    ticket_progress,
)

router = APIRouter(prefix="/api/voyage-flow", tags=["航次协作流程"])


class ReceiptCreatePayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class AttachPayload(BaseModel):
    receipt_no: str | None = None
    voyage_id: int | str | None = None


class CorrectionCreatePayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CorrectionApplyPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    hold_response: bool = False


def _result(ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok, "message": message}
    payload.update(extra)
    return payload


@router.post("/voyage-registrations")
def create_voyage_registration(payload: ReceiptCreatePayload) -> dict[str, Any]:
    """登记航次并返回航次登记回执；校验失败不写航次表，只返回驳回凭据。"""
    from app.services import service_for

    service = service_for("voyage")
    entry, error = service.create_entry(payload.values)
    if entry is None:
        if isinstance(error, str):
            return _result(False, error)
        return _result(False, f"缺少必填字段：{'、'.join(error)}")

    receipt = receipt_by_no(entry.get("回执编号"))
    return _result(
        True, "航次已登记，航次登记回执已生成",
        entry=entry,
        voyage_id=entry.get("id"),
        receipt_no=entry.get("回执编号"),
        receipt=receipt,
    )


@router.get("/voyage-registrations")
def list_voyage_registrations() -> dict[str, Any]:
    items = registration_records()
    return {"items": items, "total": len(items)}


@router.get("/voyage-registrations/{registration_no}")
def get_voyage_registration(registration_no: str) -> dict[str, Any]:
    for row in registration_records():
        if str(row.get("登记编号")) == registration_no:
            return row
    raise HTTPException(status_code=404, detail=f"登记编号 {registration_no} 不存在")


@router.post("/voyage-registrations/{registration_no}/reject")
def reject_voyage_registration(registration_no: str, reason: str = "") -> dict[str, Any]:
    for row in registration_records():
        if str(row.get("登记编号")) == registration_no:
            voyage_id = row.get("航次编号")
            if voyage_id:
                entry, message = reject_registered_voyage(voyage_id, reason)
                return _result(entry is not None, message, entry=entry)
            row["状态"] = "已驳回"
            row["驳回原因"] = reason
            return _result(True, "登记已驳回")
    raise HTTPException(status_code=404, detail=f"登记编号 {registration_no} 不存在")


@router.get("/receipts")
def list_receipts() -> dict[str, Any]:
    items = receipt_records()
    return {"items": items, "total": len(items)}


@router.get("/receipts/{receipt_no}")
def get_receipt(receipt_no: str) -> dict[str, Any]:
    receipt = receipt_by_no(receipt_no)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"回执编号 {receipt_no} 不存在")
    return receipt


@router.get("/selectable-voyages")
def get_selectable_voyages() -> dict[str, Any]:
    """货代可选航次：只含有有效登记回执的正式航次。"""
    items = selectable_voyages()
    return {"items": items, "total": len(items)}


@router.get("/pending-spaces")
def get_pending_spaces() -> dict[str, Any]:
    """舱位待确认项：原货票 + 这一笔登记的办理进度，挂接前不占装载件数。"""
    items = pending_spaces()
    return {"items": items, "total": len(items)}


@router.get("/tickets/{ticket_no}")
def get_ticket_progress(ticket_no: str) -> dict[str, Any]:
    progress = ticket_progress(ticket_no)
    if progress is None:
        raise HTTPException(status_code=404, detail=f"货票 {ticket_no} 不存在")
    return progress


@router.post("/cargo/{cargo_id}/attach")
def attach_cargo_entry(cargo_id: int, payload: AttachPayload) -> dict[str, Any]:
    """真正挂接：必须查到有效航次登记回执，成功后原货票只有一个航次编号。"""
    entry, message = attach_cargo(cargo_id, receipt_no=payload.receipt_no, voyage_id=payload.voyage_id)
    return _result(entry is not None, message, entry=entry)


@router.post("/corrections")
def create_correction_entry(payload: CorrectionCreatePayload) -> dict[str, Any]:
    correction, message = create_correction(payload.values)
    return _result(correction is not None, message, correction=correction,
                   correction_no=correction.get("更正编号") if correction else None)


@router.get("/corrections")
def list_corrections() -> dict[str, Any]:
    from app.services.voyage_flow import correction_records
    items = correction_records()
    return {"items": items, "total": len(items)}


@router.get("/corrections/{correction_no}")
def get_correction(correction_no: str) -> dict[str, Any]:
    from app.services.voyage_flow import correction_records
    for row in correction_records():
        if str(row.get("更正编号")) == correction_no:
            return row
    raise HTTPException(status_code=404, detail=f"更正编号 {correction_no} 不存在")


@router.post("/corrections/{correction_no}/apply")
def apply_correction_entry(correction_no: str, payload: CorrectionApplyPayload) -> dict[str, Any]:
    """调度送达航线更正；hold_response=true 时回执留在途中，便于验证后续挂接拦截。"""
    correction, receipt, message = apply_correction(
        correction_no, payload.values, hold=payload.hold_response,
    )
    return _result(
        correction is not None, message,
        correction=correction,
        receipt=receipt,
        voyage_id=correction.get("航次编号") if correction else None,
        receipt_no=receipt.get("回执编号") if receipt else None,
    )


@router.post("/corrections/{correction_no}/reject")
def reject_correction_entry(correction_no: str, reason: str = "") -> dict[str, Any]:
    correction, message = reject_correction(correction_no, reason)
    return _result(correction is not None, message, correction=correction)
