from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pandas as pd
import pytest

from src.loader import FILENAMES, SCHEMAS, load_files
from src.analytics import build_report

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def files():
    return {name: (ROOT / "demo_data" / name).read_bytes() for name in FILENAMES.values()}


@pytest.fixture
def demo_raw(files):
    return load_files(files)


@pytest.fixture
def valid_raw():
    records = {
        "crm_orders": [["0001", "Анна", "Салон", "Ирина", "2026-09-01", "2026-09-20", "200000", "completed"]],
        "measurements": [["0001", "Иван", "2026-09-03", "2026-09-03", "completed", "да", "да"]],
        "production": [["0001", "completed", "2026-09-05", "2026-09-15", "2026-09-15", 70000, 20000, 5000]],
        "installation": [["0001", "Пётр", "2026-09-20", "2026-09-20", "completed", 10000, 5000, "нет"]],
    }
    return {source: pd.DataFrame(rows, columns=SCHEMAS[source]) for source, rows in records.items()}


@pytest.fixture
def report_factory():
    reports = []
    def create(raw, as_of=date(2026, 10, 1)):
        report = build_report(raw, as_of)
        reports.append(report)
        return report
    yield create
    for report in reports:
        report.connection.close()


def zip_bytes(files):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()
