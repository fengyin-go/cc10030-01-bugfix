"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class MaintenanceService(BaseModuleService):
    MODULE = "maintenance"
    ENTITY = "保养记录"
    OPERATION_TERM = "核验保养"
    LIST_FIELDS = ["船舶编号", "保养项目", "保养周期", "上次保养", "下次保养", "负责人员", "保养状态", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "保养项目", "保养周期"]
    STATUS_ORDER = ["草稿", "待核验", "已核验", "已逾期"]
    ACTION_RULES = {"核验保养": "已核验", "调整周期": "待核验", "标记逾期": "已逾期"}
    NEGATIVE_ACTIONS = []
