"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CrewCertService(BaseModuleService):
    MODULE = "crew_cert"
    ENTITY = "证书记录"
    OPERATION_TERM = "核验证书"
    LIST_FIELDS = ["船员姓名", "证书类型", "证书编号", "签发机构", "签发日期", "有效期至", "下次复审", "备注说明"]
    REQUIRED_FIELDS = ["船员姓名", "证书类型", "证书编号"]
    STATUS_ORDER = ["草稿", "待核验", "已核验", "已到期"]
    ACTION_RULES = {"核验证书": "已核验", "安排复审": "待核验", "标记到期": "已到期"}
    NEGATIVE_ACTIONS = []
