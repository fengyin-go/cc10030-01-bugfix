"""航次编排领域服务：跨模块业务链路（船舶+航线→航次、租船合约→航次、航次→货物/舱单）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    charter_active_for_vessel,
    route_confirmed,
    vessel_confirmed,
)


class VoyageService(BaseModuleService):
    MODULE = "voyage"
    ENTITY = "航次"
    OPERATION_TERM = "签认航次"
    LIST_FIELDS = ["船舶编号", "航线编号", "计划离港", "计划到港", "实际离港", "实际到港", "航次状态", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "航线编号", "计划离港"]
    STATUS_ORDER = ["草稿", "待签认", "已签认", "已完成"]
    ACTION_RULES = {"签认航次": "已签认", "推迟离港": "待签认", "标记完成": "已完成"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 基础必填校验
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        # 跨模块校验：船舶必须已确认
        vessel_id = values.get("船舶编号", "")
        ok, msg = vessel_confirmed(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        # 跨模块校验：航线必须已确认
        route_id = values.get("航线编号", "")
        ok, msg = route_confirmed(route_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        # 跨模块校验：租船合约必须有效
        ok, msg = charter_active_for_vessel(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 标记完成时触发舱单待签发
        result, msg = super().run_action(entry_id, action)
        if result is not None and action == "标记完成":
            from app.services.cross_module import voyage_complete_triggers_manifest
            from app.store import store
            voyage = store.find("voyage", entry_id)
            if voyage and voyage_complete_triggers_manifest(entry_id):
                msg = f"航次已完成，请关注舱单编制"
        return result, msg
