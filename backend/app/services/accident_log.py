"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class AccidentLogService(BaseModuleService):
    MODULE = "accident_log"
    ENTITY = "事故记录"
    OPERATION_TERM = "确认事故"
    LIST_FIELDS = ["发生时间", "发生海域", "涉事船舶", "事故等级", "人员伤亡", "污染情况", "上报时间", "备注说明"]
    REQUIRED_FIELDS = ["发生时间", "发生海域", "涉事船舶"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已结案"]
    ACTION_RULES = {"确认事故": "已确认", "补充证据": "待确认", "提交结案": "已结案"}
    NEGATIVE_ACTIONS = []
