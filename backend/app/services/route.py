"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class RouteService(BaseModuleService):
    MODULE = "route"
    ENTITY = "航线"
    OPERATION_TERM = "确认航线"
    LIST_FIELDS = ["起运港", "目的港", "经停港口", "标准航程", "航线等级", "适航船舶", "最近更新", "备注说明"]
    REQUIRED_FIELDS = ["起运港", "目的港", "经停港口"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已停用"]
    ACTION_RULES = {"确认航线": "已确认", "调整经停": "已确认", "停用航线": "已停用"}
    NEGATIVE_ACTIONS = ["停用航线"]
