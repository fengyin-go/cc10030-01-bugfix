"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DispatchNoteService(BaseModuleService):
    MODULE = "dispatch_note"
    ENTITY = "指令单"
    OPERATION_TERM = "接收指令"
    LIST_FIELDS = ["接收船舶", "指令内容", "发出时间", "执行期限", "发出人员", "接收船员", "执行状态", "备注说明"]
    REQUIRED_FIELDS = ["接收船舶", "指令内容", "发出时间"]
    STATUS_ORDER = ["草稿", "待接收", "已接收", "已执行"]
    ACTION_RULES = {"接收指令": "已接收", "申请延期": "待接收", "确认执行": "已执行"}
    NEGATIVE_ACTIONS = []
