"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class InspectionService(BaseModuleService):
    MODULE = "inspection"
    ENTITY = "检验记录"
    OPERATION_TERM = "确认检验"
    LIST_FIELDS = ["船舶编号", "检验类型", "检验机构", "检验日期", "有效期至", "检验结论", "缺陷项目", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "检验类型", "检验机构"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已过期"]
    ACTION_RULES = {"确认检验": "已确认", "安排复检": "待确认", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []
