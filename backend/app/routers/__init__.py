"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

流程路由要排在通用资源路由之前：通用路由的 ``/api/{module}/{entry_id}``
会吃掉 ``/api/voyage-flow/...`` 这类两段式路径。
"""
from __future__ import annotations

from app.routers import resource, voyage_flow

ROUTERS = [voyage_flow, resource]
