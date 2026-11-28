"""航次登记回执 / 航线更正 / 货物挂接 全链路场景测试。

覆盖用户描述的真实事故链路：
- 无效航次登记不能再留下残留航次；
- 货代只凭有效回执挂货，处理中/驳回时只能进舱位待确认；
- 调度重复送达同一更正幂等；
- 在途响应窗口下原货票不占件数、最终只认一个正式航次编号。
"""
from __future__ import annotations

import threading

from app.services.cargo import CargoService
from app.services.cross_module import cargo_occupies_capacity, check_cargo_tonnage
from app.services.voyage import VoyageService
from app.services.voyage_flow import (
    ATTACH_DONE,
    RECEIPT_PROCESSING,
    RECEIPT_REJECTED,
    apply_correction,
    attach_cargo,
    create_correction,
    pending_spaces,
    receipt_by_no,
    receipt_for_voyage,
    reject_registered_voyage,
    selectable_voyages,
    ticket_progress,
)
from app.store import store


def _voyage_rows():
    return store.rows("voyage")


def _ticket_row(ticket):
    for row in store.rows("cargo"):
        if str(row.get("货票编号")) == ticket:
            return row
    return None


class TestInvalidVoyageRegistration:
    def test_nonexistent_route_leaves_no_voyage_row(self):
        svc = VoyageService()
        before = len(_voyage_rows())
        entry, result = svc.create_entry({
            "船舶编号": 3,
            "航线编号": 999,
            "计划离港": "2026-12-01",
        })
        assert entry is None
        assert len(_voyage_rows()) == before, "无效航线登记不得留下残留航次"

    def test_unconfirmed_vessel_leaves_no_voyage_row(self):
        svc = VoyageService()
        before = len(_voyage_rows())
        entry, result = svc.create_entry({
            "船舶编号": 1,
            "航线编号": 3,
            "计划离港": "2026-12-01",
        })
        assert entry is None
        assert len(_voyage_rows()) == before

    def test_missing_required_field_leaves_no_voyage_row(self):
        svc = VoyageService()
        before = len(_voyage_rows())
        entry, result = svc.create_entry({
            "船舶编号": 3,
            "航线编号": 3,
            "计划离港": "",
        })
        assert entry is None
        assert len(_voyage_rows()) == before

    def test_valid_registration_returns_receipt(self):
        svc = VoyageService()
        entry, result = svc.create_entry({
            "船舶编号": 3,
            "航线编号": 3,
            "计划离港": "2026-12-02",
        })
        assert entry is not None, result
        receipt = receipt_by_no(entry["回执编号"])
        assert receipt is not None
        assert receipt["状态"] == "有效"
        assert str(receipt["航次编号"]) == str(entry["id"])


class TestSelectableVoyages:
    def test_rejected_voyage_not_selectable(self):
        svc = VoyageService()
        entry, _ = svc.create_entry({"船舶编号": 3, "航线编号": 3, "计划离港": "2026-12-03"})
        assert entry is not None
        rejected, message = reject_registered_voyage(entry["id"], "调度驳回")
        assert rejected is not None, message

        selectable = selectable_voyages()
        ids = {int(row["voyage_id"]) for row in selectable}
        assert entry["id"] not in ids, "被驳回的航次不能出现在货代可选航次里"
        assert all(str(row.get("id")) != str(entry["id"]) for row in _voyage_rows())

    def test_processing_receipt_not_selectable(self):
        correction, message = create_correction({
            "更正编号": "CORR-SEL",
            "货票编号": "TKT-SEL",
            "船舶编号": 3,
            "航线编号": 999,
            "计划离港": "2026-12-04",
        })
        assert correction is not None, message
        _, receipt, _ = apply_correction("CORR-SEL", {"航线编号": 3}, hold=True)
        assert receipt["状态"] == RECEIPT_PROCESSING

        ids = {int(row["voyage_id"]) for row in selectable_voyages()}
        assert int(correction["航次编号"]) not in ids

    def test_all_selectable_rows_have_valid_receipt(self):
        for row in selectable_voyages():
            receipt = receipt_by_no(row.get("回执编号"))
            assert receipt is not None and receipt["状态"] == "有效"


class TestCargoPendingSpace:
    def test_cargo_without_receipt_lands_in_pending_space(self):
        cargo_svc = CargoService()
        entry, result = cargo_svc.create_entry({
            "货票编号": "TKT-PENDING-A",
            "货物品名": "钢材",
            "托运方": "货代甲",
            "件数": "50",
            "总吨数": "20",
        })
        assert entry is not None, result
        assert entry["status"] == "舱位待确认"
        assert entry.get("挂接状态") == "待挂接"

        pending = pending_spaces()
        row = next(item for item in pending if item["货票编号"] == "TKT-PENDING-A")
        assert row["原件数"] == "50"
        assert row["可挂接"] is False
        assert "办理进度" in row and row["办理进度"]

    def test_pending_cargo_does_not_consume_loaded_pieces(self):
        cargo_svc = CargoService()
        entry, _ = cargo_svc.create_entry({
            "货票编号": "TKT-PENDING-B",
            "货物品名": "矿石",
            "托运方": "货代乙",
            "件数": "50",
            "总吨数": "100",
            "装载航次": "",
        })
        assert entry is not None
        row = _ticket_row("TKT-PENDING-B")
        assert not cargo_occupies_capacity(row), "待确认舱位不能占用已装载件数"

        # 吨位校验也不应把这 50 件算进航次已用吨位
        svc = VoyageService()
        voyage, _ = svc.create_entry({"船舶编号": 3, "航线编号": 3, "计划离港": "2026-12-05"})
        assert voyage is not None
        # 种子船舶的载重吨字段是文本样例，这里只验证占用口径没有把待确认货物计入。
        occupied = [
            cargo for cargo in store.rows("cargo")
            if str(cargo.get("装载航次")) == str(voyage["id"]) and cargo_occupies_capacity(cargo)
        ]
        assert occupied == []

    def test_explicit_invalid_voyage_is_rejected(self):
        cargo_svc = CargoService()
        entry, result = cargo_svc.create_entry({
            "货物品名": "谷物",
            "托运方": "货代丙",
            "件数": "10",
            "装载航次": 999,
        })
        assert entry is None, "显式指定无效航次应被拒绝"
        assert "回执" in str(result)


class TestCorrectionIdempotency:
    def test_repeated_correction_delivery_yields_one_voyage_no(self):
        correction, message = create_correction({
            "更正编号": "CORR-IDEM",
            "货票编号": "TKT-IDEM",
            "船舶编号": 3,
            "航线编号": 3,
            "计划离港": "2026-12-06",
        })
        assert correction is not None, message
        first, receipt1, _ = apply_correction("CORR-IDEM")
        assert first["状态"] == "已生效"
        voyage_ids_after_first = {int(row["id"]) for row in _voyage_rows()}

        second, receipt2, msg = apply_correction("CORR-IDEM")
        assert second["航次编号"] == first["航次编号"]
        assert receipt2["回执编号"] == receipt1["回执编号"]
        assert {int(row["id"]) for row in _voyage_rows()} == voyage_ids_after_first, "重复送达不能新建航次"
        assert "不重复生成" in msg

    def test_concurrent_correction_delivery_yields_one_voyage_no(self):
        create_correction({
            "更正编号": "CORR-CONC",
            "货票编号": "TKT-CONC",
            "船舶编号": 3,
            "航线编号": 3,
            "计划离港": "2026-12-07",
        })
        results: list[tuple[str, str]] = []
        barrier = threading.Barrier(8)

        def deliver():
            barrier.wait()
            correction, receipt, _ = apply_correction("CORR-CONC")
            results.append((str(correction["航次编号"]), str(receipt["回执编号"]) if receipt else ""))

        threads = [threading.Thread(target=deliver) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        voyage_nos = {item[0] for item in results}
        receipt_nos = {item[1] for item in results}
        assert len(voyage_nos) == 1, "并发送达同一更正只能取得一份有效航次编号"
        assert len(receipt_nos) == 1
        created = [
            row for row in _voyage_rows()
            if str(row.get("更正编号")) == "CORR-CONC"
        ]
        assert len(created) == 1, "并发更正只允许生成一条航次"

    def test_correction_rejected_not_resendable(self):
        create_correction({
            "更正编号": "CORR-REJ",
            "货票编号": "TKT-REJ",
            "船舶编号": 3,
            "航线编号": 999,
            "计划离港": "2026-12-08",
        })
        from app.services.voyage_flow import reject_correction
        reject_correction("CORR-REJ", "航线无效")
        correction, receipt, message = apply_correction("CORR-REJ", {"航线编号": 3})
        assert correction is None or correction["状态"] == "已驳回"


class TestInFlightCorrectionFlow:
    """用户描述的主线：无效航线发起更正 → 调度改填航线 3 → 第一次响应在途。"""

    def test_full_in_flight_flow(self):
        ticket = "TKT-MAIN-50"
        # 1) 货代从无效航线的更正记录发出待挂申请，同时提交原先预录的 50 件
        correction, message = create_correction({
            "更正编号": "CORR-MAIN",
            "货票编号": ticket,
            "货物品名": "原货票货物",
            "托运方": "货代丁",
            "件数": "50",
            "船舶编号": 3,
            "航线编号": 999,
            "计划离港": "2026-12-09",
        })
        assert correction is not None, message
        assert correction["状态"] == "处理中"

        cargo_svc = CargoService()
        entry, result = cargo_svc.create_entry({
            "更正编号": "CORR-MAIN",
            "货票编号": ticket,
            "货物品名": "原货票货物",
            "托运方": "货代丁",
            "件数": "50",
        })
        assert entry is not None, result
        assert entry["status"] == "舱位待确认"
        assert _ticket_row(ticket)["件数"] == "50"

        # 2) 调度改填航线 3，第一次响应留在途中
        _, first_receipt, msg = apply_correction("CORR-MAIN", {"航线编号": 3}, hold=True)
        assert first_receipt["状态"] == RECEIPT_PROCESSING, msg
        in_flight_voyage = int(correction["航次编号"])

        # 货代在回执未返回时提交原 50 件：仍只能进舱位待确认
        entry2, result2 = cargo_svc.create_entry({
            "更正编号": "CORR-MAIN",
            "货票编号": ticket,
            "货物品名": "原货票货物",
            "托运方": "货代丁",
            "件数": "50",
        })
        assert entry2 is not None, result2
        assert entry2["status"] == "舱位待确认"
        assert "-" not in str(entry2.get("装载航次") or ""), "回执未返回时不得写入正式航次"

        pending = pending_spaces()
        row = next(item for item in pending if item["货票编号"] == ticket)
        assert row["原件数"] == "50"
        assert row["可挂接"] is False
        assert row["回执状态"] == RECEIPT_PROCESSING
        assert in_flight_voyage not in {int(v["voyage_id"]) for v in selectable_voyages()}

        # 挂接前不占已装载件数：此时航次上已装载的件数合计为 0
        occupied = sum(
            int(float(str(c.get("件数", 0))))
            for c in store.rows("cargo")
            if str(c.get("装载航次")) == str(in_flight_voyage) and cargo_occupies_capacity(c)
        )
        assert occupied == 0

        # 3) 真正挂接时必须查到有效回执：此刻回执还在处理中，挂接被拦截
        attached, attach_msg = attach_cargo(entry2["id"], voyage_id=in_flight_voyage)
        assert attached is None
        assert "不是有效回执" in attach_msg or "回执" in attach_msg

        # 4) 调度重送同一更正（第一次响应仍未返回）→ 只取得同一份有效航次编号
        second, final_receipt, final_msg = apply_correction("CORR-MAIN", {"航线编号": 3})
        assert final_receipt["状态"] == "有效", final_msg
        assert str(second["航次编号"]) == str(in_flight_voyage), "重复送达不得新建第二个航次"
        assert len([v for v in _voyage_rows() if str(v.get("更正编号")) == "CORR-MAIN"]) == 1

        # 5) 回执有效后才允许挂接，原 50 件接到唯一的正式航次
        attached, attach_msg = attach_cargo(entry2["id"], receipt_no=final_receipt["回执编号"])
        assert attached is not None, attach_msg
        assert attached["挂接状态"] == ATTACH_DONE
        assert str(attached["装载航次"]) == str(in_flight_voyage)
        assert attached["航次编号"] == str(in_flight_voyage)

        # 6) 两边看到同一笔正式航次，原货票只有一个号码
        selected_ids = {int(v["voyage_id"]) for v in selectable_voyages()}
        assert in_flight_voyage in selected_ids
        progress = ticket_progress(ticket)
        assert progress["航次编号"] == str(in_flight_voyage)
        assert progress["回执编号"] == final_receipt["回执编号"]

        ticket_rows = [c for c in store.rows("cargo") if str(c.get("货票编号")) == ticket]
        active = [c for c in ticket_rows if str(c.get("装载航次") or "") not in ("", "None")]
        assert len(active) == 1, "原货票最终只有一个正式航次编号"
        assert set(str(c["装载航次"]) for c in active) == {str(in_flight_voyage)}
        assert progress["原货票"] == ticket

    def test_duplicate_ticket_submission_cannot_bypass_receipt(self):
        ticket = "TKT-DUP-50"
        create_correction({
            "更正编号": "CORR-DUP",
            "货票编号": ticket,
            "船舶编号": 3,
            "航线编号": 999,
            "计划离港": "2026-12-10",
        })
        _, receipt, _ = apply_correction("CORR-DUP", {"航线编号": 3}, hold=True)
        cargo_svc = CargoService()

        for _ in range(2):
            entry, _ = cargo_svc.create_entry({
                "更正编号": "CORR-DUP",
                "货票编号": ticket,
                "货物品名": "预录货物",
                "托运方": "货代戊",
                "件数": "50",
            })
            assert entry["status"] == "舱位待确认"

        rows = [c for c in store.rows("cargo") if str(c.get("货票编号")) == ticket]
        assert len(rows) == 1, "同一原货票的重复提交应合并到一条待确认记录"


class TestAttachRequiresValidReceipt:
    def test_rejected_receipt_cannot_attach(self):
        # 种子航次 + 被驳回的登记凭据：原货票不能挂到驳回航次上
        svc = VoyageService()
        voyage, _ = svc.create_entry({"船舶编号": 3, "航线编号": 3, "计划离港": "2026-12-11"})
        reject_registered_voyage(voyage["id"], "资料不全")
        receipt = receipt_for_voyage(voyage["id"])
        assert receipt is None or receipt["状态"] == RECEIPT_REJECTED

        cargo_svc = CargoService()
        entry, _ = cargo_svc.create_entry({
            "货票编号": "TKT-REJECTED",
            "货物品名": "杂货",
            "托运方": "货代己",
            "件数": "10",
        })
        attached, message = attach_cargo(entry["id"], voyage_id=voyage["id"])
        assert attached is None
        assert "回执" in message or "不存在" in message

    def test_attach_is_idempotent(self):
        cargo_svc = CargoService()
        voyage_svc = VoyageService()
        voyage, _ = voyage_svc.create_entry({"船舶编号": 3, "航线编号": 3, "计划离港": "2026-12-12"})
        receipt = receipt_for_voyage(voyage["id"])
        entry, _ = cargo_svc.create_entry({
            "货票编号": "TKT-IDEM-ATTACH",
            "货物品名": "设备",
            "托运方": "货代庚",
            "件数": "5",
            "装载航次": voyage["id"],
        })
        first, first_msg = attach_cargo(entry["id"], receipt_no=receipt["回执编号"])
        assert first is not None, first_msg
        second, second_msg = attach_cargo(entry["id"], receipt_no=receipt["回执编号"])
        assert second is not None
        assert str(second["装载航次"]) == str(voyage["id"])
        assert "不重复挂接" in second_msg
