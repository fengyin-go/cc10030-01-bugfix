"""港口挂靠领域服务：跨模块业务链路（航次→挂靠、挂靠→通关）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import port_call_in_sequence, voyage_exists


class PortCallService(BaseModuleService):
    MODULE = "port_call"
    ENTITY = "挂靠记录"
    OPERATION_TERM = "确认挂靠"
    LIST_FIELDS = ["航次编号", "挂靠港口", "预计到港", "实际到港", "离港时间", "停留原因", "代理人员", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "挂靠港口", "预计到港"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已离港"]
    ACTION_RULES = {"确认挂靠": "已确认", "延长停留": "待确认", "申报离港": "已离港"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 跨模块校验必须在创建条目之前执行，避免自身条目影响顺序判断

        # 跨模块校验：航次必须存在
        voyage_id = values.get("航次编号", "")
        if voyage_id:
            ok, msg = voyage_exists(voyage_id)
            if not ok:
                return None, f"挂靠登记失败：{msg}"

        # 跨模块校验：前一个挂靠必须已离港
        if voyage_id:
            ok, msg = port_call_in_sequence(voyage_id)
            if not ok:
                return None, f"挂靠登记失败：{msg}"

        # 基础必填校验和创建
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        return entry, []
