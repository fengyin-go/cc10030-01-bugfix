"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class TariffService(BaseModuleService):
    MODULE = "tariff"
    ENTITY = "费率单"
    OPERATION_TERM = "审定费率"
    LIST_FIELDS = ["服务项目", "计费单位", "标准费率", "优惠费率", "适用航线", "生效日期", "失效日期", "备注说明"]
    REQUIRED_FIELDS = ["服务项目", "计费单位", "标准费率"]
    STATUS_ORDER = ["草稿", "待审定", "已审定", "已作废"]
    ACTION_RULES = {"审定费率": "已审定", "退回修订": "草稿", "作废费率": "已作废"}
    NEGATIVE_ACTIONS = ["作废费率"]
