"""通关申报领域服务：跨模块业务链路（挂靠→通关）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import port_call_arrived, voyage_exists


class ClearanceService(BaseModuleService):
    MODULE = "clearance"
    ENTITY = "申报单"
    OPERATION_TERM = "审核申报"
    LIST_FIELDS = ["航次编号", "申报类型", "申报港口", "提交时间", "审批状态", "海关编号", "有效期", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "申报类型", "申报港口"]
    STATUS_ORDER = ["草稿", "待审核", "审核通过", "已失效"]
    ACTION_RULES = {"审核申报": "审核通过", "退回补充": "草稿", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 基础必填校验
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        # 跨模块校验：航次必须存在
        voyage_id = values.get("航次编号", "")
        if voyage_id:
            ok, msg = voyage_exists(voyage_id)
            if not ok:
                return None, f"申报登记失败：{msg}"

        return entry, []
