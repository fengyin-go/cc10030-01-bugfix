"""货物登记领域服务：跨模块业务链路（航次→货物、货物→舱单）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import check_cargo_tonnage, voyage_not_completed


class CargoService(BaseModuleService):
    MODULE = "cargo"
    ENTITY = "货物"
    OPERATION_TERM = "确认装载"
    LIST_FIELDS = ["货物品名", "托运方", "件数", "总吨数", "装载航次", "装货港", "卸货港", "备注说明"]
    REQUIRED_FIELDS = ["货物品名", "托运方", "件数"]
    STATUS_ORDER = ["草稿", "待装载", "已装载", "已卸货"]
    ACTION_RULES = {"确认装载": "已装载", "调整数量": "已装载", "确认卸货": "已卸货"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 跨模块校验必须在创建条目之前执行，避免自身条目被重复计入吨位

        # 跨模块校验：装载航次必须存在且未完成
        voyage_id = values.get("装载航次", "")
        if voyage_id:
            ok, msg = voyage_not_completed(voyage_id)
            if not ok:
                return None, f"货物登记失败：{msg}"

        # 跨模块校验：总吨位不能超过船舶载重吨
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
        return entry, []
