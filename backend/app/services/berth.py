"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class BerthService(BaseModuleService):
    MODULE = "berth"
    ENTITY = "泊位安排"
    OPERATION_TERM = "确认泊位"
    LIST_FIELDS = ["港口名称", "泊位编号", "靠泊船舶", "计划靠泊", "计划离泊", "实际靠泊", "实际离泊", "备注说明"]
    REQUIRED_FIELDS = ["港口名称", "泊位编号", "靠泊船舶"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已完成"]
    ACTION_RULES = {"确认泊位": "已确认", "调整时间": "待确认", "标记完成": "已完成"}
    NEGATIVE_ACTIONS = []
