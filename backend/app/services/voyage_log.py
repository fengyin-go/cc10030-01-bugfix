"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class VoyageLogService(BaseModuleService):
    MODULE = "voyage_log"
    ENTITY = "日志条目"
    OPERATION_TERM = "签阅日志"
    LIST_FIELDS = ["航次编号", "记录时刻", "船位经纬度", "航速节", "海况等级", "事件描述", "值班人员", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "记录时刻", "船位经纬度"]
    STATUS_ORDER = ["草稿", "待签阅", "已签阅", "已封存"]
    ACTION_RULES = {"签阅日志": "已签阅", "修正记录": "草稿", "封存日志": "已封存"}
    NEGATIVE_ACTIONS = []
