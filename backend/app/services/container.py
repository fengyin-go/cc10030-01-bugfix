"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ContainerService(BaseModuleService):
    MODULE = "container"
    ENTITY = "集装箱"
    OPERATION_TERM = "确认签封"
    LIST_FIELDS = ["箱号", "箱型尺寸", "封条编号", "装载航次", "装卸港口", "货主名称", "温度要求", "备注说明"]
    REQUIRED_FIELDS = ["箱号", "箱型尺寸", "封条编号"]
    STATUS_ORDER = ["草稿", "待签封", "已签封", "已交付"]
    ACTION_RULES = {"确认签封": "已签封", "标记异常": "待签封", "确认交付": "已交付"}
    NEGATIVE_ACTIONS = []
