from datetime import datetime

import pandas as pd
import pytest

from src.validator import parse_bool, parse_date, parse_money, validate_sources


def test_valid_sources_have_no_issues(valid_raw):
    clean, issues = validate_sources(valid_raw)
    assert issues.empty
    assert clean["measurements"].loc[0, "design_approved"] == 1
    assert clean["crm_orders"].loc[0, "sale_amount"] == 200000


def test_duplicate(demo_raw):
    _, issues = validate_sources(demo_raw)
    duplicates = issues.loc[issues.issue_type == "Дубликат"]
    assert duplicates[["order_id", "source"]].to_dict("records") == [{"order_id": "003", "source": "production"}]


def test_orphans_and_missing(demo_raw):
    _, issues = validate_sources(demo_raw)
    assert set(issues.loc[issues.issue_type == "Нет заказа в CRM", "order_id"]) == {"888", "999"}
    missing = issues.loc[issues.issue_type == "Отсутствует источник"]
    assert set(zip(missing.order_id, missing.source)) == {("005", "production"), ("005", "installation")}


def test_status_conflict_and_missing_completion(demo_raw):
    _, issues = validate_sources(demo_raw)
    conflicts = issues.loc[issues.issue_type == "Расхождение статусов"]
    assert set(conflicts.order_id) == {"002"}
    logical = issues.loc[issues.issue_type == "Логическое противоречие"]
    assert any((logical.order_id == "002") & (logical.source == "production"))


def test_negative_invalid_date_and_required(demo_raw):
    _, issues = validate_sources(demo_raw)
    assert set(issues.loc[issues.issue_type == "Отрицательная сумма", "order_id"]) == {"004"}
    assert set(issues.loc[issues.issue_type == "Некорректная дата", "order_id"]) == {"005"}
    assert any((issues.order_id == "006") & (issues.issue_type == "Обязательное поле"))
    assert set(issues.severity) <= {"Ошибка", "Предупреждение"}


@pytest.mark.parametrize("value,expected", [("25.09.2026", "2026-09-25"), ("2026-09-25", "2026-09-25"), (datetime(2026, 9, 25), "2026-09-25")])
def test_date_formats(value, expected):
    assert parse_date(value) == expected


@pytest.mark.parametrize("value", ["31.02.2026", "garbage", "09/25/2026", 45000])
def test_invalid_dates(value):
    with pytest.raises(ValueError):
        parse_date(value)


@pytest.mark.parametrize("value,expected", [("1 234,56", 1234.56), ("0", 0), ("-10", -10)])
def test_money_formats(value, expected):
    assert parse_money(value) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", "1.234", "1e3", "text", "1000000000001"])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        parse_money(value)


def test_bool():
    assert parse_bool("нет") == 0
    assert parse_bool(True) == 1
    with pytest.raises(ValueError):
        parse_bool("возможно")


def test_invalid_status_and_bool_and_blank_id(valid_raw):
    valid_raw["measurements"].loc[0, "design_approved"] = "возможно"
    valid_raw["production"].loc[0, "production_stage"] = "unknown"
    valid_raw["installation"].loc[0, "order_id"] = " "
    clean, issues = validate_sources(valid_raw)
    assert clean["production"].loc[0, "production_stage"] is None
    assert sum(issues.issue_type == "Некорректное значение") == 2
    assert any((issues.order_id == "(без номера)") & (issues.issue_type == "Обязательное поле"))


def test_duplicate_rows_are_all_validated(valid_raw):
    bad = valid_raw["production"].copy()
    bad.loc[0, "materials_cost"] = -5
    valid_raw["production"] = pd.concat([valid_raw["production"], bad], ignore_index=True)
    _, issues = validate_sources(valid_raw)
    negative = issues.loc[issues.issue_type == "Отрицательная сумма"].iloc[0]
    assert negative.row_number == 3


def test_dates_out_of_sequence(valid_raw):
    valid_raw["production"].loc[0, "actual_finish"] = "2026-09-04"
    _, issues = validate_sources(valid_raw)
    assert any(issues.description == "Производство завершено раньше начала.")


def test_planned_finish_before_start_and_specification_without_design(valid_raw):
    valid_raw["production"].loc[0, "planned_finish"] = "2026-09-04"
    valid_raw["measurements"].loc[0, "design_approved"] = "нет"
    _, issues = validate_sources(valid_raw)
    assert any(issues.description == "Плановое окончание производства раньше его начала.")
    assert any(issues.description == "Спецификация готова, но проект не утверждён.")


def test_normalization_keeps_raw_unchanged(valid_raw):
    before = {source: frame.copy(deep=True) for source, frame in valid_raw.items()}
    validate_sources(valid_raw)
    for source, frame in valid_raw.items():
        pd.testing.assert_frame_equal(frame, before[source])
