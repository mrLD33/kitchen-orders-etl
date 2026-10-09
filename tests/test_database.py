import pandas as pd


def test_separate_tables_retain_duplicate_and_unparsed_values(demo_raw, report_factory):
    report = report_factory(demo_raw)
    connection = report.connection
    assert connection.execute("SELECT COUNT(*) FROM production WHERE order_id='003'").fetchone()[0] == 2
    assert connection.execute("SELECT order_date FROM crm_orders WHERE order_id='005'").fetchone()[0] == "31.02.2026"
    assert connection.execute("SELECT COUNT(*) FROM orders_view").fetchone()[0] == 6
    assert connection.execute("SELECT materials_cost FROM orders_view WHERE order_id='003'").fetchone()[0] == 40000


def test_join_does_not_multiply_rows_in_any_source(valid_raw, report_factory):
    for source in valid_raw:
        valid_raw[source] = pd.concat([valid_raw[source], valid_raw[source]], ignore_index=True)
    report = report_factory(valid_raw)
    assert len(report.orders) == 1
    assert report.orders.iloc[0].order_id == "0001"
    assert report.orders.iloc[0].finance_status == "Неоднозначные (дубли)"
    for source in valid_raw:
        assert report.connection.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0] == 2


def test_left_join_preserves_crm_without_excel(valid_raw, report_factory):
    valid_raw["production"] = valid_raw["production"].iloc[:0]
    report = report_factory(valid_raw)
    assert len(report.orders) == 1
    assert pd.isna(report.orders.iloc[0].production_order_id)
    assert pd.isna(report.orders.iloc[0].margin_percent)


def test_orphans_are_separate_and_all_rows_kept(demo_raw, report_factory):
    report = report_factory(demo_raw)
    assert set(report.orphans.order_id) == {"888", "999"}
    assert set(report.orders.order_id) == {"001", "002", "003", "004", "005", "006"}


def test_sessions_use_independent_memory_databases(valid_raw, report_factory):
    first = report_factory(valid_raw)
    valid_raw["crm_orders"].loc[0, "client_name"] = "Другой клиент"
    second = report_factory(valid_raw)
    assert first.connection is not second.connection
    assert first.connection.execute("SELECT client_name FROM crm_orders").fetchone()[0] == "Анна"
    assert second.connection.execute("SELECT client_name FROM crm_orders").fetchone()[0] == "Другой клиент"
    assert first.connection.execute("PRAGMA database_list").fetchone()[2] == ""


def test_blank_id_not_joined_to_blank_id(valid_raw, report_factory):
    for frame in valid_raw.values():
        frame.loc[0, "order_id"] = ""
    report = report_factory(valid_raw)
    assert report.orders.empty
    assert len(report.orphans) == 3
