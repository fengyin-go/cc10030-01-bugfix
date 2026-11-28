"""货物登记领域服务：跨模块业务链路（航次→货物、货物→舱单）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    check_cargo_tonnage,
    voyage_not_completed,
)
from app.services.voyage_flow import (
    ATTACH_DONE,
    PENDING_SPACE,
    RECEIPT_VALID,
    attach_cargo,
    find_cargo_by_ticket,
    receipt_by_no,
    receipt_for_voyage,
    upsert_pending_cargo,
)


class CargoService(BaseModuleService):
    MODULE = "cargo"
    ENTITY = "货物"
    OPERATION_TERM = "确认装载"
    LIST_FIELDS = ["货物品名", "托运方", "件数", "总吨数", "装载航次", "装货港", "卸货港", "备注说明"]
    REQUIRED_FIELDS = ["货物品名", "托运方", "件数"]
    STATUS_ORDER = ["草稿", "待装载", "已装载", "已卸货"]
    ACTION_RULES = {
        "确认装载": "已装载",
        "调整数量": "已装载",
        "确认卸货": "已卸货",
        "挂接航次": "待装载",
    }
    ACTION_FROM = {
        "确认装载": ["草稿", "待装载"],
        "调整数量": ["草稿", "待装载", "已装载"],
        "挂接航次": [PENDING_SPACE, "待装载", "草稿", "已装载"],
    }
    NEGATIVE_ACTIONS = []

    def _has_valid_receipt(self, values: dict[str, Any]) -> tuple[bool, str]:
        """登记时是否已拿到有效航次登记回执。"""
        receipt_no = str(values.get("回执编号") or "").strip()
        if receipt_no:
            receipt = receipt_by_no(receipt_no)
            if receipt is None:
                return False, f"航次登记回执 {receipt_no} 不存在"
            return receipt.get("状态") == RECEIPT_VALID, f"航次登记回执 {receipt_no} 状态为「{receipt.get('状态')}」"
        voyage_id = values.get("装载航次", "")
        if voyage_id:
            receipt = receipt_for_voyage(voyage_id)
            if receipt is None:
                return False, f"航次 {voyage_id} 没有航次登记回执"
            return receipt.get("状态") == RECEIPT_VALID, f"航次 {voyage_id} 的登记回执状态为「{receipt.get('状态')}」"
        return False, "缺少有效航次登记回执"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 跨模块校验必须在创建条目之前执行，避免自身条目被重复计入吨位

        ticket_no = str(values.get("货票编号") or "").strip()
        existing = find_cargo_by_ticket(ticket_no) if ticket_no else None
        if existing is not None and existing.get("挂接状态") == ATTACH_DONE:
            return existing, f"原货票 {ticket_no} 已挂接航次 {existing.get('装载航次')}，不重复登记"

        has_receipt, receipt_message = self._has_valid_receipt(values)
        voyage_id = values.get("装载航次", "")

        # 没有有效回执：只能进「舱位待确认」，不得真正挂接，也不占装载件数。
        if not has_receipt:
            if str(values.get("装载航次") or "").strip():
                # 显式指定了航次却拿不到有效回执，说明登记本身不成立
                return None, f"货物登记失败：{receipt_message}"
            entry, progress_message = upsert_pending_cargo(values)
            return entry, f"货物已进入舱位待确认：{receipt_message}；{progress_message}"

        # 有有效回执：按正式航次登记，校验航次状态与吨位。
        if voyage_id:
            ok, msg = voyage_not_completed(voyage_id)
            if not ok:
                return None, f"货物登记失败：{msg}"

        tonnage_str = str(values.get("总吨数", "0"))
        try:
            tonnage = float(tonnage_str)
        except (ValueError, TypeError):
            tonnage = 0.0
        if tonnage > 0 and voyage_id:
            ok, msg = check_cargo_tonnage(voyage_id, tonnage)
            if not ok:
                return None, f"货物登记失败：{msg}"

        # 基础必填校验和创建
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        receipt_no = str(values.get("回执编号") or "").strip()
        if not receipt_no:
            receipt = receipt_for_voyage(voyage_id)
            receipt_no = str(receipt.get("回执编号") or "") if receipt else ""
        entry["回执编号"] = receipt_no
        entry["挂接状态"] = ATTACH_DONE
        if ticket_no:
            entry["货票编号"] = ticket_no
        if values.get("更正编号"):
            entry["更正编号"] = values.get("更正编号")
        entry["航次编号"] = str(entry.get("装载航次", voyage_id))
        entry["办理进度"] = f"已挂接航次 {entry.get('装载航次', voyage_id)}（回执 {receipt_no}）"
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"货物 {entry_id} 不存在或已归档"

        if action == "挂接航次":
            # 真正挂接时必须查到有效航次登记回执
            result, message = attach_cargo(entry_id)
            return result, message

        allowed = self.ACTION_FROM.get(action)
        if allowed is not None and entry.get("status") not in allowed:
            return None, f"货物 {entry_id} 当前状态为「{entry.get('status')}」，不能执行「{action}」"

        # 未真正挂接的原货票不能直接确认装载，避免绕过回执校验
        if action == "确认装载" and entry.get("挂接状态") != ATTACH_DONE:
            result, message = attach_cargo(entry_id)
            if result is None:
                return None, message
            return self.run_action(entry_id, "确认装载")

        return super().run_action(entry_id, action)
