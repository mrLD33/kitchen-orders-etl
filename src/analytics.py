"""Direct-cost profitability and deadlines with explicit reliability flags."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import sqlite3

import pandas as pd

from src.database import create_database, read_orders, read_orphans
from src.validator import ISSUE_COLUMNS, is_blank, issue, validate_sources

COST_FIELDS = ["materials_cost", "labor_cost", "other_cost", "installation_cost", "delivery_cost"]
STAGES = {
    "Замер": ("measurement_planned", "measurement_actual", "measurement_status"),
    "Производство": ("production_planned", "production_actual", "production_stage"),
    "Монтаж": ("installation_planned", "installation_actual", "installation_status"),
    "Срок заказа": ("planned_delivery", "installation_actual", "installation_status"),
}
STATUS_LABELS = {"new": "Новый", "measurement": "Замер", "design": "Проектирование",
                 "production": "Производство", "ready": "Готов к монтажу",
                 "installation": "Монтаж", "completed": "Завершён", "cancelled": "Отменён"}
ORDER_LABELS = {
    "order_id": "Номер заказа", "client_name": "Клиент", "dealer": "Дилер",
    "manager": "Менеджер", "crm_status": "Статус CRM", "current_stage": "Текущий этап",
    "planned_delivery": "Плановый срок", "installation_actual": "Фактический срок",
    "overdue": "Просрочка", "overdue_days": "Дней просрочки", "overdue_stages": "Этапы просрочки",
    "sale_amount": "Стоимость заказа", "total_cost": "Прямые затраты",
    "gross_profit": "Валовая прибыль по прямым затратам", "margin_percent": "Маржинальность, %",
    "finance_status": "Полнота финансовых данных", "has_errors": "Наличие ошибок",
    "has_problems": "Наличие проблем", "incomplete": "Неполные данные",
    "measurement_planned": "План замера", "measurement_actual": "Факт замера",
    "production_planned": "План производства", "production_actual": "Факт производства",
    "installation_planned": "План монтажа",
}
ISSUE_LABELS = {"order_id": "Номер заказа", "issue_type": "Тип проблемы",
                "source": "Источник", "description": "Описание", "severity": "Критичность",
                "row_number": "Строка источника"}


@dataclass
class Report:
    connection: sqlite3.Connection
    orders: pd.DataFrame
    issues: pd.DataFrame
    orphans: pd.DataFrame
    deadlines: pd.DataFrame
    counts: dict
    as_of: date


def calculate_finance(row, ambiguous=False, invalid_data=False):
    values = [row.get(field) for field in ["sale_amount"] + COST_FIELDS]
    missing = any(is_blank(value) for value in values)
    invalid = any(not is_blank(value) and value < 0 for value in values)
    invalid = invalid or (not is_blank(row.get("sale_amount")) and row["sale_amount"] == 0)
    status = ("Неоднозначные (дубли)" if ambiguous else "Некорректные" if invalid or invalid_data
              else "Неполные" if missing else "Полные")
    if status != "Полные":
        return {"total_cost": None, "gross_profit": None, "margin_percent": None, "finance_status": status}
    cost = sum((Decimal(str(row[field])) for field in COST_FIELDS), Decimal(0))
    sale = Decimal(str(row["sale_amount"]))
    profit = sale - cost
    return {"total_cost": float(cost), "gross_profit": float(profit),
            "margin_percent": float(profit / sale * 100), "finance_status": status}


def deadline_days(planned, actual, status, as_of, cancelled=False):
    if cancelled or status == "cancelled" or is_blank(planned):
        return None
    planned_date = date.fromisoformat(planned)
    if not is_blank(actual):
        # Future actual dates are a data error, not evidence of completed work.
        if date.fromisoformat(actual) > as_of:
            return None
        return max(0, (date.fromisoformat(actual) - planned_date).days)
    if status == "completed":
        return None  # completed with unknown actual date: deadline cannot be verified
    return max(0, (as_of - planned_date).days)


def current_stage(row):
    if row["crm_status"] == "cancelled":
        return "Отменён"
    if row["installation_status"] == "completed" and not is_blank(row["installation_actual"]):
        return "Завершён"
    if row["installation_status"] == "in_progress" or row["production_stage"] == "completed":
        return "Монтаж"
    if row["production_stage"] == "in_progress":
        return "Производство"
    if row["measurement_status"] == "completed":
        return "Производство" if row["design_approved"] == 1 and row["specification_ready"] == 1 else "Проектирование"
    return "Замер"


def build_report(raw, as_of=None):
    as_of = as_of or date.today()
    clean, issue_frame = validate_sources(raw)
    connection = create_database(raw, clean)
    try:
        orders = read_orders(connection)
        extra, deadlines = [], []
        duplicates = issue_frame.loc[(issue_frame.issue_type == "Дубликат") &
                                     issue_frame.source.isin(["crm_orders", "production", "installation"]), "order_id"]
        duplicate_ids = set(duplicates)
        invalid_finance_ids = set(issue_frame.loc[
            (issue_frame.issue_type == "Некорректное значение") &
            issue_frame.description.str.contains("sale_amount|materials_cost|labor_cost|other_cost|installation_cost|delivery_cost"),
            "order_id"])
        finance_columns = ["total_cost", "gross_profit", "margin_percent", "finance_status"]
        derived_columns = ["current_stage", "overdue", "overdue_days", "overdue_stages"]
        derived = []
        for row in orders.to_dict("records"):
            finance = calculate_finance(row, row["order_id"] in duplicate_ids,
                                        row["order_id"] in invalid_finance_ids)
            if not is_blank(row["sale_amount"]) and row["sale_amount"] == 0:
                extra.append(issue(row["order_id"], "Нулевая стоимость", "crm_orders", "Маржинальность не определена при нулевой стоимости заказа."))
            late_stages, max_days = [], 0
            for stage, (planned, actual, status) in STAGES.items():
                days = deadline_days(row[planned], row[actual], row[status], as_of, row["crm_status"] == "cancelled")
                deadlines.append({"order_id": row["order_id"], "stage": stage,
                                  "planned_date": row[planned], "actual_date": row[actual],
                                  "overdue_days": days, "overdue": days is not None and days > 0})
                if days is not None and days > 0:
                    late_stages.append(stage)
                    max_days = max(max_days, days)
                    extra.append(issue(row["order_id"], "Просрочка", stage,
                                       f"Плановый срок нарушен на {days} дн. Дата оценки: {as_of.isoformat()}.", "Предупреждение"))
            derived.append({**finance, "current_stage": current_stage(row),
                            "overdue": bool(late_stages), "overdue_days": max_days,
                            "overdue_stages": ", ".join(late_stages)})
        # Future actual dates must also be found in duplicates and orphan rows.
        for source, frame in clean.items():
            fields = {"crm_orders": ["order_date"], "measurements": ["actual_date"],
                      "production": ["production_start", "actual_finish"], "installation": ["actual_date"]}[source]
            for row in frame.to_dict("records"):
                for field in fields:
                    if row[field] and date.fromisoformat(row[field]) > as_of:
                        extra.append(issue(row["order_id"], "Логическое противоречие", source,
                                           f"Дата {field} находится в будущем относительно даты оценки.", row_number=row["row_number"]))
        for column in finance_columns + derived_columns:
            orders[column] = pd.Series([item[column] for item in derived], index=orders.index,
                                       dtype=object if column in {"finance_status", "current_stage", "overdue_stages"} else None)
        issues = pd.DataFrame(issue_frame.to_dict("records") + extra, columns=ISSUE_COLUMNS)
        error_ids = set(issues.loc[issues.severity == "Ошибка", "order_id"])
        problem_ids = set(issues.order_id)
        incomplete_ids = set(issues.loc[issues.issue_type.isin(["Обязательное поле", "Отсутствует источник"]), "order_id"])
        orders["has_errors"] = orders.order_id.isin(error_ids)
        orders["has_problems"] = orders.order_id.isin(problem_ids)
        orders["incomplete"] = orders.order_id.isin(incomplete_ids) | (orders.finance_status != "Полные")
        return Report(connection, orders, issues, read_orphans(connection),
                      pd.DataFrame(deadlines, columns=["order_id", "stage", "planned_date", "actual_date", "overdue_days", "overdue"]),
                      {source: len(frame) for source, frame in raw.items()}, as_of)
    except Exception:
        connection.close()
        raise


def metrics(orders):
    valid = orders.loc[orders.finance_status == "Полные"]
    sale = sum((Decimal(str(value)) for value in valid.sale_amount), Decimal(0))
    profit = sum((Decimal(str(value)) for value in valid.gross_profit), Decimal(0))
    positive_sales = orders.loc[orders.sale_amount.notna() & (orders.sale_amount >= 0), "sale_amount"]
    return {"orders": len(orders), "overdue": int(orders.overdue.sum()),
            "errors": int(orders.has_errors.sum()), "incomplete": int(orders.incomplete.sum()),
            "sales": float(sum((Decimal(str(value)) for value in positive_sales), Decimal(0))),
            "margin": float(profit / sale * 100) if sale else None, "valid_finance": len(valid)}


def export_csv(frame, labels=None):
    # Escape spreadsheet formulas in arbitrary uploaded text, including IDs and names.
    safe = frame.copy()
    def escape(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value
    safe = safe.map(escape)
    if labels:
        safe = safe.rename(columns=labels)
    return safe.to_csv(index=False, sep=";").encode("utf-8-sig")
