"""船员排班领域服务：跨模块业务链路（航次→船员、证书→排班）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    check_crew_shift_overlap,
    crew_cert_valid_for_shift,
    voyage_not_completed,
)


class CrewShiftService(BaseModuleService):
    MODULE = "crew_shift"
    ENTITY = "排班单"
    OPERATION_TERM = "确认排班"
    LIST_FIELDS = ["船员姓名", "岗位", "上船日期", "下船日期", "所属航次", "替班人员", "在船状态", "备注说明"]
    REQUIRED_FIELDS = ["船员姓名", "岗位", "上船日期"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已下船"]
    ACTION_RULES = {"确认排班": "已确认", "安排替班": "已确认", "标记下船": "已下船"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 跨模块校验必须在创建条目之前执行，避免自身条目标志为重叠

        # 跨模块校验：所属航次必须存在且未完成
        voyage_id = values.get("所属航次", "")
        if voyage_id:
            ok, msg = voyage_not_completed(voyage_id)
            if not ok:
                return None, f"排班失败：{msg}"

        # 跨模块校验：同一船员不能跨重叠航次排班
        crew_name = values.get("船员姓名", "")
        embark_date = str(values.get("上船日期", ""))
        disembark_date = str(values.get("下船日期", ""))
        if crew_name and embark_date and disembark_date:
            ok, msg = check_crew_shift_overlap(crew_name, embark_date, disembark_date)
            if not ok:
                return None, f"排班失败：{msg}"

        # 跨模块校验：船员证书必须有效
        if crew_name:
            ok, msg = crew_cert_valid_for_shift(crew_name)
            if not ok:
                return None, f"排班失败：{msg}"

        # 基础必填校验和创建
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        return entry, []
