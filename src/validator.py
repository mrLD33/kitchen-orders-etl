"""Normalize cells without losing the original data; collect row and business issues."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
import re

import pandas as pd

from src.loader import SCHEMAS

ISSUE_COLUMNS = ["order_id", "issue_type", "source", "description", "severity", "row_number"]
DATE_FIELDS = {
    "crm_orders": {"order_date", "planned_delivery"},
    "measurements": {"planned_date", "actual_date"},
    "production": {"production_start", "planned_finish", "actual_finish"},
    "installation": {"planned_date", "actual_date"},
}
MONEY_FIELDS = {"sale_amount", "materials_cost", "labor_cost", "other_cost",
                "installation_cost", "delivery_cost"}
BOOL_FIELDS = {"design_approved", "specification_ready"}
OPTIONAL = {"actual_date", "production_start", "actual_finish", "complaint"}
STATUS_FIELDS = {"crm_orders": "crm_status", "measurements": "measurement_status",
                 "production": "production_stage", "installation": "installation_status"}
ALIASES = {
    "crm_orders": {
        "new": ["new", "новый"], "measurement": ["measurement", "замер"],
        "design": ["design", "проектирование", "дизайн"],
        "production": ["production", "в производстве", "производство"],
        "ready": ["ready", "готов к монтажу", "готов"],
        "installation": ["installation", "монтаж", "в монтаже"],
        "completed": ["completed", "завершён", "завершен", "завершённый", "выполнен"],
        "cancelled": ["cancelled", "отменён", "отменен"],
    },
    "measurements": {
        "planned": ["planned", "запланирован", "запланировано"],
        "in_progress": ["in_progress", "в работе", "в процессе"],
        "completed": ["completed", "выполнен", "завершён", "завершен"],
        "cancelled": ["cancelled", "отменён", "отменен"],
    },
    "production": {
        "planned": ["planned", "запланировано", "не начато"],
        "in_progress": ["in_progress", "в работе", "в производстве"],
        "completed": ["completed", "завершено", "готово"],
        "cancelled": ["cancelled", "отменено"],
    },
    "installation": {
        "planned": ["planned", "запланирован", "запланировано"],
        "in_progress": ["in_progress", "в работе", "в монтаже"],
        "completed": ["completed", "выполнен", "завершён", "завершен"],
        "cancelled": ["cancelled", "отменён", "отменен"],
    },
}


def is_blank(value):
    return value is None or pd.isna(value) or (isinstance(value, str) and not value.strip())


def parse_date(value):
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(str(value).strip(), pattern)
            return parsed.date().isoformat()
        except ValueError:
            continue
    raise ValueError("Некорректная дата")


def parse_money(value):
    text = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise ValueError("Некорректная сумма")
    try:
        number = Decimal(text)
        if not number.is_finite() or abs(number) > Decimal("1000000000000"):
            raise ValueError("Сумма вне диапазона")
        if number != number.quantize(Decimal("0.01")):
            raise ValueError("Более двух десятичных знаков")
        return float(number)
    except InvalidOperation as exc:
        raise ValueError("Некорректная сумма") from exc


def parse_bool(value):
    text = str(value).strip().lower()
    if text in {"true", "1", "1.0", "да", "yes"}:
        return 1
    if text in {"false", "0", "0.0", "нет", "no"}:
        return 0
    raise ValueError("Ожидается да/нет")


def issue(order_id, kind, source, description, severity="Ошибка", row_number=None):
    return dict(zip(ISSUE_COLUMNS, [order_id or "(без номера)", kind, source,
                                   description, severity, row_number]))


def validate_sources(raw):
    cleaned, issues = {}, []
    for source, frame in raw.items():
        rows = []
        for offset, original in enumerate(frame.to_dict("records"), start=2):
            row = {}
            order = original["order_id"]
            order_id = None if is_blank(order) else str(order).strip()
            if isinstance(order, float) and math.isfinite(order) and order.is_integer():
                order_id = str(int(order))
            for field in SCHEMAS[source]:
                value = original[field]
                if is_blank(value):
                    row[field] = None
                    if field not in OPTIONAL:
                        issues.append(issue(order_id, "Обязательное поле", source,
                                            f"Не заполнено поле {field}.", row_number=offset))
                    continue
                try:
                    if field == "order_id":
                        row[field] = order_id
                    elif field in DATE_FIELDS[source]:
                        row[field] = parse_date(value)
                    elif field in MONEY_FIELDS:
                        row[field] = parse_money(value)
                        if row[field] < 0:
                            issues.append(issue(order_id, "Отрицательная сумма", source,
                                                f"Поле {field} содержит отрицательное значение.", row_number=offset))
                    elif field in BOOL_FIELDS:
                        row[field] = parse_bool(value)
                    elif field == STATUS_FIELDS[source]:
                        aliases = {alias: key for key, values in ALIASES[source].items() for alias in values}
                        row[field] = aliases[str(value).strip().lower()]
                    else:
                        row[field] = str(value).strip()
                except (ValueError, KeyError):
                    row[field] = None
                    kind = "Некорректная дата" if field in DATE_FIELDS[source] else "Некорректное значение"
                    issues.append(issue(order_id, kind, source, f"Некорректное значение поля {field}.", row_number=offset))
            row["row_number"] = offset
            rows.append(row)
        clean = pd.DataFrame(rows, columns=SCHEMAS[source] + ["row_number"]).astype(object)
        clean = clean.where(pd.notna(clean), None)
        cleaned[source] = clean
        duplicate_ids = clean.loc[clean.order_id.notna() & clean.order_id.duplicated(keep=False), "order_id"].unique()
        for order_id in duplicate_ids:
            count = int((clean.order_id == order_id).sum())
            issues.append(issue(order_id, "Дубликат", source,
                                f"Найдено записей: {count}. В реестре используется первая строка файла."))

    # Deterministic first-row policy, identical to the SQL view, including bad first rows.
    maps = {source: {row["order_id"]: row for row in frame.dropna(subset=["order_id"])
                    .drop_duplicates("order_id", keep="first").to_dict("records")}
            for source, frame in cleaned.items()}
    crm = maps["crm_orders"]
    for source in ("measurements", "production", "installation"):
        for order_id in maps[source].keys() - crm.keys():
            issues.append(issue(order_id, "Нет заказа в CRM", source, "Запись не имеет соответствия в CRM."))
        for order_id in crm.keys() - maps[source].keys():
            issues.append(issue(order_id, "Отсутствует источник", source, "Для заказа отсутствует запись в источнике."))

    # Row-level contradictions are checked for every original row, not only the selected one.
    for source in ("measurements", "production", "installation"):
        for row in cleaned[source].to_dict("records"):
            status = row[STATUS_FIELDS[source]]
            actual_field = "actual_finish" if source == "production" else "actual_date"
            actual = row[actual_field]
            if status == "completed" and not actual:
                issues.append(issue(row["order_id"], "Логическое противоречие", source,
                                    "Этап завершён, но фактическая дата отсутствует.", row_number=row["row_number"]))
            if actual and status != "completed":
                issues.append(issue(row["order_id"], "Логическое противоречие", source,
                                    "Фактическая дата указана, но статус этапа не завершён.", row_number=row["row_number"]))
            if source == "production" and status in {"in_progress", "completed"} and not row["production_start"]:
                issues.append(issue(row["order_id"], "Обязательное поле", source,
                                    "Начатое производство не имеет даты production_start.", row_number=row["row_number"]))
            if source == "production" and row["production_start"] and actual and actual < row["production_start"]:
                issues.append(issue(row["order_id"], "Логическое противоречие", source,
                                    "Производство завершено раньше начала.", row_number=row["row_number"]))
            if source == "production" and row["production_start"] and row["planned_finish"] and row["planned_finish"] < row["production_start"]:
                issues.append(issue(row["order_id"], "Логическое противоречие", source,
                                    "Плановое окончание производства раньше его начала.", row_number=row["row_number"]))
            if source == "measurements" and row["specification_ready"] == 1 and row["design_approved"] == 0:
                issues.append(issue(row["order_id"], "Логическое противоречие", source,
                                    "Спецификация готова, но проект не утверждён.", row_number=row["row_number"]))

    for order_id, order in crm.items():
        measurement = maps["measurements"].get(order_id, {})
        production = maps["production"].get(order_id, {})
        installation = maps["installation"].get(order_id, {})
        if order["order_date"] and order["planned_delivery"] and order["planned_delivery"] < order["order_date"]:
            issues.append(issue(order_id, "Логическое противоречие", "crm_orders", "Поставка запланирована раньше даты заказа."))
        if order["crm_status"] == "completed" and not (installation.get("installation_status") == "completed" and installation.get("actual_date")):
            issues.append(issue(order_id, "Расхождение статусов", "CRM / монтаж", "CRM: заказ завершён; завершение монтажа не подтверждено."))
        rank = {"new": 0, "measurement": 1, "design": 2, "production": 3,
                "ready": 4, "installation": 5, "completed": 6}
        crm_rank = rank.get(order["crm_status"], -1)
        if crm_rank >= 4 and production.get("production_stage") != "completed":
            issues.append(issue(order_id, "Расхождение статусов", "CRM / производство", "CRM показывает готовность к монтажу; производство не завершено."))
        if crm_rank >= 3 and not (measurement.get("measurement_status") == "completed" and measurement.get("design_approved") == 1 and measurement.get("specification_ready") == 1):
            issues.append(issue(order_id, "Расхождение статусов", "CRM / замер", "CRM показывает производство или более поздний этап; замер, проект или спецификация не готовы."))
        if production.get("production_stage") in {"in_progress", "completed"} and not (
                measurement.get("measurement_status") == "completed" and measurement.get("design_approved") == 1 and measurement.get("specification_ready") == 1):
            issues.append(issue(order_id, "Логическое противоречие", "замер / производство", "Производство начато без завершённого замера, утверждённого проекта или спецификации."))
        if installation.get("installation_status") in {"in_progress", "completed"} and not (
                production.get("production_stage") == "completed" and production.get("actual_finish")):
            issues.append(issue(order_id, "Логическое противоречие", "производство / монтаж", "Монтаж начат без подтверждённого завершения производства."))
        if installation.get("installation_status") == "completed" and order["crm_status"] not in {"completed", "cancelled"}:
            issues.append(issue(order_id, "Расхождение статусов", "CRM / монтаж", "Монтаж завершён, но CRM не показывает завершённый заказ.", "Предупреждение"))
        if order["crm_status"] == "cancelled" and any([
                production.get("production_stage") == "in_progress", installation.get("installation_status") == "in_progress"]):
            issues.append(issue(order_id, "Расхождение статусов", "CRM / этапы", "Отменённый заказ находится в работе."))
        for source, record, fields in (("measurements", measurement, ["actual_date"]),
                                       ("production", production, ["production_start", "actual_finish"]),
                                       ("installation", installation, ["actual_date"])):
            for field in fields:
                if record.get(field) and order["order_date"] and record[field] < order["order_date"]:
                    issues.append(issue(order_id, "Логическое противоречие", source, f"Дата {field} раньше даты заказа."))
        if measurement.get("actual_date") and production.get("production_start") and production["production_start"] < measurement["actual_date"]:
            issues.append(issue(order_id, "Логическое противоречие", "замер / производство", "Производство началось раньше замера."))
        if production.get("actual_finish") and installation.get("actual_date") and installation["actual_date"] < production["actual_finish"]:
            issues.append(issue(order_id, "Логическое противоречие", "производство / монтаж", "Монтаж выполнен раньше окончания производства."))
    return cleaned, pd.DataFrame(issues, columns=ISSUE_COLUMNS)
