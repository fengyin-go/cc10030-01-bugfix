"""航次编排领域服务：跨模块业务链路（船舶+航线→航次、租船合约→航次、航次→货物/舱单）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    charter_active_for_vessel,
    route_confirmed,
    vessel_confirmed,
)
from app.services.voyage_flow import register_voyage, reject_registered_voyage


class VoyageService(BaseModuleService):
    MODULE = "voyage"
    ENTITY = "航次"
    OPERATION_TERM = "签认航次"
    LIST_FIELDS = ["船舶编号", "航线编号", "计划离港", "计划到港", "实际离港", "实际到港", "航次状态", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "航线编号", "计划离港"]
    STATUS_ORDER = ["草稿", "待签认", "已签认", "已完成"]
    ACTION_RULES = {"签认航次": "已签认", "推迟离港": "待签认", "标记完成": "已完成", "驳回登记": "已驳回"}
    ACTION_FROM = {
        "签认航次": ["草稿", "待签认"],
        "推迟离港": ["草稿", "待签认", "已签认"],
        "驳回登记": ["草稿", "待签认"],
    }
    NEGATIVE_ACTIONS = ["驳回登记"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 必填校验在落库之前完成，缺字段时不产生任何记录
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        # 跨模块校验：船舶已确认 + 航线已确认 + 租船合约有效
        # 全部通过后才写航次；任一失败只留一条驳回凭据，绝不留下残留航次。
        vessel_id = values.get("船舶编号", "")
        ok, msg = vessel_confirmed(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        route_id = values.get("航线编号", "")
        ok, msg = route_confirmed(route_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        ok, msg = charter_active_for_vessel(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        entry, receipt, message = register_voyage(values)
        if entry is None or receipt is None:
            return None, message or "创建航次失败：登记未通过"
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 驳回登记：回执转驳回并把航次移出列表/货代可选范围
        if action == "驳回登记":
            return reject_registered_voyage(entry_id)

        # 状态流转前置条件
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档"
        allowed = self.ACTION_FROM.get(action)
        if allowed is not None and entry.get("status") not in allowed:
            return None, f"航次 {entry_id} 当前状态为「{entry.get('status')}」，不能执行「{action}」"

        # 标记完成时触发舱单待签发
        result, msg = super().run_action(entry_id, action)
        if result is not None and action == "标记完成":
            from app.services.cross_module import voyage_complete_triggers_manifest
            from app.store import store
            voyage = store.find("voyage", entry_id)
            if voyage and voyage_complete_triggers_manifest(entry_id):
                msg = "航次已完成，请关注舱单编制"
        return result, msg
