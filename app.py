"""Русский интерфейс ETL. Все пользовательские данные находятся в session_state."""
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import plotly.express as px
import streamlit as st

from src.analytics import (ISSUE_LABELS, ORDER_LABELS, STATUS_LABELS, build_report,
                           export_csv, metrics)
from src.loader import FILENAMES, LoadError, load_files, load_zip

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Контроль заказов кухонь", layout="wide")
st.title("Заказы кухонь: консолидация и контроль")
st.caption("Четыре источника → единый реестр, качество данных, сроки и предварительная маржинальность.")
as_of = st.sidebar.date_input("Дата оценки сроков", value=date.today())
st.sidebar.caption("Незавершённые этапы сравниваются с этой датой. Пустая фактическая дата сама по себе не означает просрочку.")


def replace_report(raw):
    # Build first: a rejected upload must not destroy the previous valid dataset.
    report = build_report(raw, as_of)
    previous = st.session_state.get("report")
    st.session_state["report"] = report
    st.session_state["raw"] = raw
    if previous is not None:
        previous.connection.close()


def demo_files():
    return {name: (ROOT / "demo_data" / name).read_bytes() for name in FILENAMES.values()}


def present_orders(frame):
    view = frame[list(ORDER_LABELS)].copy()
    view["crm_status"] = view.crm_status.map(STATUS_LABELS).fillna("Не определён")
    for column in ("overdue", "has_errors", "has_problems", "incomplete"):
        view[column] = view[column].map({True: "Да", False: "Нет"})
    return view.rename(columns=ORDER_LABELS)


upload_tab, overview_tab, orders_tab, quality_tab, export_tab = st.tabs([
    "1. Загрузка данных", "2. Общая аналитика", "3. Реестр заказов",
    "4. Контроль качества", "5. Отчёты"])

with upload_tab:
    st.write("Нужны crm_orders.csv, measurements.xlsx, production.xlsx и installation.xlsx.")
    st.caption("CSV: UTF-8, разделитель запятая или точка с запятой. Excel: первый лист, заголовки в первой строке. До 10 МБ и 50 000 строк на источник; ZIP до 20 МБ, распакованные исходники до 40 МБ.")
    mode = st.radio("Способ загрузки", ["Демонстрационные данные", "ZIP-архив", "Четыре файла"], horizontal=True)
    try:
        if mode == "Демонстрационные данные":
            if st.button("Загрузить демонстрационные данные", type="primary"):
                replace_report(load_files(demo_files()))
                st.success("Демонстрационные данные загружены.")
            bundle = BytesIO()
            with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
                for name, data in demo_files().items():
                    archive.writestr(name, data)
            st.download_button("Скачать демонстрационный ZIP", bundle.getvalue(), "kitchen_demo.zip", "application/zip")
        elif mode == "ZIP-архив":
            uploaded = st.file_uploader("ZIP с четырьмя источниками", type=["zip"], key="zip_upload")
            if st.button("Загрузить ZIP", disabled=uploaded is None, type="primary"):
                replace_report(load_zip(uploaded.getvalue()))
                st.success("ZIP обработан. Ошибки содержимого доступны в контроле качества.")
        else:
            uploads = {name: st.file_uploader(name, type=[name.rsplit(".", 1)[1]], key=name)
                       for name in FILENAMES.values()}
            if st.button("Загрузить четыре файла", disabled=any(item is None for item in uploads.values()), type="primary"):
                replace_report(load_files({name: item.getvalue() for name, item in uploads.items()}))
                st.success("Четыре источника обработаны.")
    except LoadError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Не удалось обработать набор данных. Предыдущий отчёт сохранён. Проверьте файлы и журнал сервера.")
        import logging
        logging.getLogger(__name__).exception("Ошибка построения отчёта")
    if "report" in st.session_state:
        st.write("Количество сохранённых исходных строк:", st.session_state.report.counts)
        if st.button("Очистить данные сессии"):
            st.session_state.report.connection.close()
            del st.session_state["report"]
            del st.session_state["raw"]
            st.rerun()

if "report" not in st.session_state:
    st.info("Загрузите четыре источника или запустите демонстрационный сценарий.")
    st.stop()

if st.session_state.report.as_of != as_of:
    replace_report(st.session_state.raw)
report = st.session_state.report
orders, issues = report.orders, report.issues

with overview_tab:
    summary = metrics(orders)
    columns = st.columns(4)
    for column, label, key in zip(columns, ["Заказов", "Просроченных", "С ошибками", "С неполными данными"],
                                  ["orders", "overdue", "errors", "incomplete"]):
        column.metric(label, summary[key])
    left, right = st.columns(2)
    left.metric("Стоимость заказов по учтённым значениям", f"{summary['sales']:,.2f}")
    right.metric("Маржинальность по полным данным", "Нет данных" if summary["margin"] is None else f"{summary['margin']:.2f}%")
    st.caption(f"Оценка на {as_of:%d.%m.%Y}. Финансово полных заказов: {summary['valid_finance']}. Общая маржинальность взвешена по выручке. Сумма заказов исключает отрицательные и непрочитанные значения; для дублей CRM берётся первая запись.")
    st.info("Маржинальность предварительная, по учтённым прямым затратам. Она не является бухгалтерской прибылью. Фактический срок заказа здесь — дата монтажа, поскольку отдельного факта поставки нет.")
    if not orders.empty:
        stages = orders.groupby("current_stage").size().reset_index(name="Количество")
        st.plotly_chart(px.bar(stages, x="current_stage", y="Количество", labels={"current_stage": "Этап"}, title="Заказы по этапам"), use_container_width=True, config={"responsive": True})
        late = report.deadlines.loc[report.deadlines.overdue].groupby("stage").size().reset_index(name="Количество")
        if not late.empty:
            st.plotly_chart(px.bar(late, x="stage", y="Количество", labels={"stage": "Этап"}, title="Просрочки по этапам"), use_container_width=True, config={"responsive": True})
        valid = orders.loc[orders.finance_status == "Полные"]
        if not valid.empty:
            st.plotly_chart(px.histogram(valid, x="margin_percent", nbins=12, labels={"margin_percent": "Маржинальность, %"}, title="Распределение предварительной маржинальности"), use_container_width=True, config={"responsive": True})

with orders_tab:
    st.caption("Фильтры применяются к реестру. Общая аналитика показывает весь загруженный набор.")
    filter_columns = st.columns(4)
    statuses = filter_columns[0].multiselect("Статус CRM", sorted(orders.crm_status.dropna().unique()), format_func=lambda value: STATUS_LABELS.get(value, value))
    managers = filter_columns[1].multiselect("Менеджер", sorted(orders.manager.dropna().unique()))
    stages = filter_columns[2].multiselect("Текущий этап", sorted(orders.current_stage.unique()))
    problems = filter_columns[3].selectbox("Наличие проблем", ["Все", "С проблемами", "Без проблем"])
    filtered_orders = orders.copy()
    for column, selected in (("crm_status", statuses), ("manager", managers), ("current_stage", stages)):
        if selected:
            filtered_orders = filtered_orders.loc[filtered_orders[column].isin(selected)]
    if problems != "Все":
        filtered_orders = filtered_orders.loc[filtered_orders.has_problems == (problems == "С проблемами")]
    st.dataframe(present_orders(filtered_orders), hide_index=True, width="stretch")
    st.caption("Дубли: в реестре первая строка каждого источника. Финансовые дубли в CRM, производстве или монтаже исключают расчёт маржинальности. Дни просрочки заказа — максимум по этапам, а не сумма.")

with quality_tab:
    types = st.multiselect("Тип проблемы", sorted(issues.issue_type.unique()))
    severities = st.multiselect("Уровень критичности", ["Ошибка", "Предупреждение"])
    filtered_issues = issues.copy()
    if types:
        filtered_issues = filtered_issues.loc[filtered_issues.issue_type.isin(types)]
    if severities:
        filtered_issues = filtered_issues.loc[filtered_issues.severity.isin(severities)]
    st.write(f"Найдено проблем: {len(issues)}; отображается: {len(filtered_issues)}.")
    st.dataframe(filtered_issues.rename(columns=ISSUE_LABELS), hide_index=True, width="stretch")
    st.subheader("Записи Excel без заказа в CRM")
    st.dataframe(report.orphans.rename(columns={"source": "Источник", "order_id": "Номер заказа", "row_number": "Строка"}), hide_index=True, width="stretch")
    with st.expander("Исходные записи (включая дубли и непрочитанные значения)"):
        for source, frame in st.session_state.raw.items():
            st.write(FILENAMES[source])
            st.dataframe(frame, hide_index=True, width="stretch")

with export_tab:
    filtered = st.checkbox("Выгрузить с учётом фильтров реестра и ошибок")
    st.download_button("Скачать реестр заказов (CSV)", export_csv(present_orders(filtered_orders if filtered else orders)),
                       "orders_report.csv", "text/csv")
    st.download_button("Скачать реестр ошибок (CSV)", export_csv(filtered_issues if filtered else issues, ISSUE_LABELS),
                       "quality_report.csv", "text/csv")
    st.caption("UTF-8 с BOM, разделитель — точка с запятой. Формулы в текстовых полях экранируются для безопасного открытия в Excel.")
