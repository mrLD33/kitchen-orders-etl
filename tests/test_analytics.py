from datetime import date
from io import BytesIO

import pandas as pd
import pytest

from src.analytics import COST_FIELDS, calculate_finance, deadline_days, export_csv, metrics
from src.loader import load_zip
from conftest import zip_bytes


def test_demo_finance_and_metrics(demo_raw, report_factory):
    report = report_factory(demo_raw)
    orders = report.orders.set_index("order_id")
    assert orders.loc["001", "total_cost"] == 110000
    assert orders.loc["001", "gross_profit"] == 90000
    assert orders.loc["001", "margin_percent"] == 45
    assert orders.loc["002", "total_cost"] == 86000
    assert orders.loc["002", "margin_percent"] == pytest.approx(42.6666666667)
    assert orders.loc["003", "finance_status"] == "Неоднозначные (дубли)"
    assert orders.loc["004", "finance_status"] == "Некорректные"
    assert orders.loc["005", "finance_status"] == "Некорректные"
    assert orders.loc["006", "finance_status"] == "Неполные"
    assert orders.loc[["003", "004", "005", "006"], "margin_percent"].isna().all()
    assert metrics(report.orders) == {"orders": 6, "overdue": 2, "errors": 5,
                                      "incomplete": 4, "sales": 650000.0,
                                      "margin": 44.0, "valid_finance": 2}


def test_deadline_complete_late_and_unfinished(demo_raw, report_factory):
    report = report_factory(demo_raw)
    deadlines = report.deadlines.set_index(["order_id", "stage"])
    assert deadlines.loc[("002", "Замер"), "overdue_days"] == 2
    assert deadlines.loc[("002", "Монтаж"), "overdue_days"] == 6
    assert pd.isna(deadlines.loc[("002", "Производство"), "overdue_days"])
    assert deadlines.loc[("004", "Производство"), "overdue_days"] == 9
    assert not deadlines.loc[("003", "Производство"), "overdue"]
    assert set(report.orders.loc[report.orders.overdue, "order_id"]) == {"002", "004"}


@pytest.mark.parametrize("planned,actual,status,expected", [
    ("2026-10-10", None, "planned", 0),
    ("2026-10-01", None, "planned", 0),
    ("2026-09-29", None, "in_progress", 2),
    ("2026-09-29", "2026-09-30", "completed", 1),
    ("2026-10-01", "2026-09-30", "completed", 0),
    (None, None, "planned", None),
    ("2026-09-29", None, "completed", None),
    ("2026-09-29", None, "cancelled", None),
    ("2026-09-29", "2026-10-05", "completed", None),
])
def test_deadline_rules(planned, actual, status, expected):
    assert deadline_days(planned, actual, status, date(2026, 10, 1)) == expected


@pytest.mark.parametrize("field", COST_FIELDS)
def test_missing_each_cost_suppresses_margin(field, valid_raw, report_factory):
    source = "production" if field in COST_FIELDS[:3] else "installation"
    valid_raw[source].loc[0, field] = None
    row = report_factory(valid_raw).orders.iloc[0]
    assert row.finance_status == "Неполные"
    assert pd.isna(row.margin_percent)
    assert pd.isna(row.total_cost)


@pytest.mark.parametrize("field", ["sale_amount"] + COST_FIELDS)
def test_negative_each_finance_value(field, valid_raw, report_factory):
    source = "crm_orders" if field == "sale_amount" else "production" if field in COST_FIELDS[:3] else "installation"
    valid_raw[source].loc[0, field] = -1
    report = report_factory(valid_raw)
    row = report.orders.iloc[0]
    assert row.finance_status == "Некорректные"
    assert pd.isna(row.margin_percent)
    assert any(report.issues.issue_type == "Отрицательная сумма")


def test_zero_sale_and_zero_cost(valid_raw, report_factory):
    valid_raw["crm_orders"].loc[0, "sale_amount"] = "0"
    report = report_factory(valid_raw)
    assert pd.isna(report.orders.iloc[0].margin_percent)
    assert any(report.issues.issue_type == "Нулевая стоимость")
    row = {"sale_amount": 100, **{field: 0 for field in COST_FIELDS}}
    assert calculate_finance(row) == {"total_cost": 0, "gross_profit": 100,
                                      "margin_percent": 100, "finance_status": "Полные"}


def test_invalid_money_is_not_reliable(valid_raw, report_factory):
    valid_raw["production"]["materials_cost"] = "не число"
    report = report_factory(valid_raw)
    assert report.orders.iloc[0].finance_status == "Некорректные"
    assert pd.isna(report.orders.iloc[0].margin_percent)


def test_decimal_cost_and_negative_profit():
    row = {"sale_amount": 1, "materials_cost": 0.1, "labor_cost": 0.2,
           "other_cost": 0.3, "installation_cost": 0.4, "delivery_cost": 0.1}
    assert calculate_finance(row) == {"total_cost": 1.1, "gross_profit": -0.1,
                                      "margin_percent": -10, "finance_status": "Полные"}


def test_future_actual_date_reported(valid_raw, report_factory):
    valid_raw["installation"].loc[0, "actual_date"] = "2026-10-05"
    report = report_factory(valid_raw)
    assert any(report.issues.description.str.contains("находится в будущем"))
    assert not report.orders.iloc[0].overdue


def test_cancelled_order_not_overdue(valid_raw, report_factory):
    valid_raw["crm_orders"].loc[0, "crm_status"] = "cancelled"
    valid_raw["installation"].loc[0, "installation_status"] = "planned"
    valid_raw["installation"].loc[0, "actual_date"] = None
    report = report_factory(valid_raw)
    assert not report.orders.iloc[0].overdue
    assert report.orders.iloc[0].current_stage == "Отменён"


def test_zip_to_sql_to_export_known_results(files, report_factory):
    report = report_factory(load_zip(zip_bytes(files)))
    exported = export_csv(report.orders)
    assert exported.startswith(b"\xef\xbb\xbf")
    result = pd.read_csv(BytesIO(exported), sep=";", dtype={"order_id": str})
    assert len(result) == 6
    assert result.loc[result.order_id == "001", "margin_percent"].iloc[0] == 45
    issues = pd.read_csv(BytesIO(export_csv(report.issues)), sep=";")
    assert "Дубликат" in set(issues.issue_type)
    assert set(issues.loc[issues.issue_type == "Нет заказа в CRM", "order_id"].astype(str)) == {"888", "999"}


def test_csv_export_escapes_formulas_without_changing_numbers():
    source = pd.DataFrame({"text": ["=HYPERLINK(\"x\")", " +SUM(1)", "@test", "-text", "Обычный текст"],
                           "amount": [-5, 0, 2, 3, 4]})
    result = pd.read_csv(BytesIO(export_csv(source)), sep=";")
    assert result.text.tolist() == ["'=HYPERLINK(\"x\")", "' +SUM(1)", "'@test", "'-text", "Обычный текст"]
    assert result.amount.tolist() == [-5, 0, 2, 3, 4]
    assert source.text.iloc[0].startswith("=")


def test_empty_crm_has_no_fabricated_orders(valid_raw, report_factory):
    valid_raw["crm_orders"] = valid_raw["crm_orders"].iloc[:0]
    report = report_factory(valid_raw)
    assert report.orders.empty
    assert len(report.orphans) == 3
    summary = metrics(report.orders)
    assert summary["orders"] == 0
    assert summary["margin"] is None
