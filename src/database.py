"""Session-local SQLite. Raw records and normalized records are stored separately."""
import sqlite3

import pandas as pd

from src.loader import SCHEMAS
from src.validator import BOOL_FIELDS, MONEY_FIELDS, is_blank

ORDER_VIEW_SQL = """
CREATE VIEW orders_view AS
WITH
c AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY row_number) AS rn FROM normalized_crm_orders WHERE order_id IS NOT NULL),
m AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY row_number) AS rn FROM normalized_measurements WHERE order_id IS NOT NULL),
p AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY row_number) AS rn FROM normalized_production WHERE order_id IS NOT NULL),
i AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY row_number) AS rn FROM normalized_installation WHERE order_id IS NOT NULL)
SELECT c.order_id, c.client_name, c.dealer, c.manager, c.order_date, c.planned_delivery,
 c.sale_amount, c.crm_status,
 m.order_id AS measurement_order_id, m.measurer, m.planned_date AS measurement_planned,
 m.actual_date AS measurement_actual, m.measurement_status, m.design_approved, m.specification_ready,
 p.order_id AS production_order_id, p.production_stage, p.production_start,
 p.planned_finish AS production_planned, p.actual_finish AS production_actual,
 p.materials_cost, p.labor_cost, p.other_cost,
 i.order_id AS installation_order_id, i.installer, i.planned_date AS installation_planned,
 i.actual_date AS installation_actual, i.installation_status, i.installation_cost, i.delivery_cost, i.complaint
FROM c
LEFT JOIN m ON m.order_id = c.order_id AND m.rn = 1
LEFT JOIN p ON p.order_id = c.order_id AND p.rn = 1
LEFT JOIN i ON i.order_id = c.order_id AND i.rn = 1
WHERE c.rn = 1
"""


def create_database(raw, cleaned):
    # Never cache/share this connection or put uploaded data in a filesystem database.
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        for source, columns in SCHEMAS.items():
            original = raw[source][columns].map(lambda value: None if is_blank(value) else str(value))
            original.insert(0, "row_number", range(2, len(original) + 2))
            original.to_sql(source, connection, index=False, if_exists="fail")
            types = {column: "REAL" if column in MONEY_FIELDS else "INTEGER" if column in BOOL_FIELDS or column == "row_number" else "TEXT"
                     for column in cleaned[source].columns}
            cleaned[source].to_sql("normalized_" + source, connection, index=False, if_exists="fail", dtype=types)
            connection.execute(f'CREATE INDEX idx_{source}_order ON normalized_{source}(order_id, row_number)')
        connection.execute(ORDER_VIEW_SQL)
        return connection
    except Exception:
        connection.close()
        raise


def read_orders(connection):
    return pd.read_sql_query("SELECT * FROM orders_view ORDER BY order_id", connection)


def read_orphans(connection):
    frames = []
    for source in ("measurements", "production", "installation"):
        frame = pd.read_sql_query(f"""
            SELECT s.* FROM normalized_{source} s
            WHERE s.order_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM normalized_crm_orders c WHERE c.order_id = s.order_id)
        """, connection)
        frame.insert(0, "source", source)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)
