"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class FuelLogService(BaseModuleService):
    MODULE = "fuel_log"
    ENTITY = "燃料记录"
    OPERATION_TERM = "核验燃料"
    LIST_FIELDS = ["船舶编号", "加油港口", "燃料类型", "加油数量", "单价", "加油日期", "经手人员", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "加油港口", "燃料类型"]
    STATUS_ORDER = ["草稿", "待核验", "已核验", "已结算"]
    ACTION_RULES = {"核验燃料": "已核验", "退回修正": "草稿", "提交结算": "已结算"}
    NEGATIVE_ACTIONS = []
