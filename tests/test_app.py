from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from conftest import zip_bytes

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_demo_interface_filters_reports_and_recalculates():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    app.date_input[0].set_value(date(2026, 10, 1)).run()
    app.button[0].click().run()
    assert not app.exception
    assert [item.value for item in app.metric] == ["6", "2", "5", "4", "650,000.00", "44.00%"]
    assert len(app.get("download_button")) == 3
    assert len(app.get("plotly_chart")) == 3
    app.multiselect[1].set_value(["Ирина"]).run()
    registry = app.dataframe[0].value
    assert set(registry["Номер заказа"]) == {"001", "002", "005"}
    app.date_input[0].set_value(date(2026, 9, 24)).run()
    assert not app.exception
    assert app.metric[1].value == "2"  # 002 has a completed late measurement; 004 has late production
    report = app.session_state["report"]
    assert report.as_of == date(2026, 9, 24)
    report.connection.close()


def test_independent_streamlit_sessions_and_clear():
    first = AppTest.from_file(str(APP), default_timeout=30).run()
    first.button[0].click().run()
    second = AppTest.from_file(str(APP), default_timeout=30).run()
    assert "report" not in second.session_state
    assert len(second.metric) == 0
    clear_button = next(button for button in first.button if button.label == "Очистить данные сессии")
    clear_button.click().run()
    assert "report" not in first.session_state
    assert len(first.metric) == 0
    assert not first.exception


def test_upload_modes_require_all_inputs():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio[0].set_value("ZIP-архив").run()
    assert not app.exception
    assert len(app.get("file_uploader")) == 1
    assert app.button[0].disabled
    app.radio[0].set_value("Четыре файла").run()
    assert not app.exception
    assert len(app.get("file_uploader")) == 4
    assert app.button[0].disabled


def test_zip_upload_interface(files):
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.date_input[0].set_value(date(2026, 10, 1)).run()
    # AppTest has no upload setter: simulate only the byte-stream upload boundary.
    with patch("streamlit.file_uploader", return_value=BytesIO(zip_bytes(files))):
        app.radio[0].set_value("ZIP-архив").run()
        app.button[0].click().run()
    assert not app.exception
    assert app.metric[0].value == "6"
    assert app.metric[5].value == "44.00%"
    assert app.session_state["report"].connection.execute("SELECT COUNT(*) FROM production").fetchone()[0] == 6
    app.session_state["report"].connection.close()


def test_individual_upload_interface(files):
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.date_input[0].set_value(date(2026, 10, 1)).run()
    with patch("streamlit.file_uploader", side_effect=lambda label, **kwargs: BytesIO(files[label])):
        app.radio[0].set_value("Четыре файла").run()
        app.button[0].click().run()
    assert not app.exception
    assert app.metric[0].value == "6"
    assert app.metric[5].value == "44.00%"
    app.session_state["report"].connection.close()


def test_rejected_upload_preserves_previous_report():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.button[0].click().run()
    previous = app.session_state["report"]
    with patch("streamlit.file_uploader", return_value=BytesIO(b"not a zip")):
        app.radio[0].set_value("ZIP-архив").run()
        app.button[0].click().run()
    assert not app.exception
    assert "Не удалось прочитать ZIP" in app.error[0].value
    assert app.session_state["report"] is previous
    assert previous.connection.execute("SELECT COUNT(*) FROM crm_orders").fetchone()[0] == 6
    previous.connection.close()
