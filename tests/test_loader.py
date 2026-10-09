from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from src.loader import LoadError, SCHEMAS, load_files, load_zip, read_source
from conftest import zip_bytes


def test_csv_preserves_leading_zero_and_utf8(files):
    frame = read_source("crm_orders", files["crm_orders.csv"])
    assert len(frame) == 6
    assert frame.loc[0, "order_id"] == "001"
    assert frame.loc[0, "client_name"] == "Анна Смирнова"


@pytest.mark.parametrize("source,count", [("measurements", 7), ("production", 6), ("installation", 6)])
def test_excel(source, count, files):
    frame = read_source(source, files[source + ".xlsx"])
    assert len(frame) == count
    assert frame.loc[0, "order_id"] == "001"
    assert list(frame.columns) == SCHEMAS[source]


def test_zip_nested_directory(files):
    loaded = load_zip(zip_bytes({"input/" + name: data for name, data in files.items()}))
    assert {key: len(value) for key, value in loaded.items()} == {"crm_orders": 6, "measurements": 7, "production": 6, "installation": 6}


@pytest.mark.parametrize("zipped", [False, True])
def test_missing_file(zipped, files):
    del files["production.xlsx"]
    with pytest.raises(LoadError, match="production.xlsx"):
        load_zip(zip_bytes(files)) if zipped else load_files(files)


def test_missing_columns():
    with pytest.raises(LoadError, match="отсутствуют столбцы"):
        read_source("crm_orders", b"order_id,client_name\n001,Anna\n")


def test_excel_missing_column(valid_raw):
    buffer = BytesIO()
    valid_raw["production"].drop(columns="materials_cost").to_excel(buffer, index=False)
    with pytest.raises(LoadError, match="materials_cost"):
        read_source("production", buffer.getvalue())


@pytest.mark.parametrize("name", ["../crm_orders.csv", "/crm_orders.csv", "C:/crm_orders.csv", "foo\\crm_orders.csv"])
def test_unsafe_path(name, files):
    with pytest.raises(LoadError, match="небезопасный путь"):
        load_zip(zip_bytes({**files, name: b"x"}))


def test_symlink(files):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        entry = ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        archive.writestr(entry, "crm_orders.csv")
    with pytest.raises(LoadError, match="Символические ссылки"):
        load_zip(buffer.getvalue())


def test_duplicate_zip_filename(files):
    with pytest.raises(LoadError, match="несколько файлов"):
        load_zip(zip_bytes({**files, "nested/crm_orders.csv": files["crm_orders.csv"]}))


@pytest.mark.parametrize("reader,content", [(load_zip, b"bad zip"), (lambda data: read_source("production", data), b"bad xlsx")])
def test_corrupt_format(reader, content):
    with pytest.raises(LoadError, match="прочитать"):
        reader(content)


def test_csv_semicolon(valid_raw):
    data = valid_raw["crm_orders"].to_csv(index=False, sep=";").encode("utf-8-sig")
    assert read_source("crm_orders", data).loc[0, "sale_amount"] == "200000"


def test_malformed_csv_row(valid_raw):
    data = valid_raw["crm_orders"].to_csv(index=False).replace("completed\n", "completed,extra\n").encode()
    with pytest.raises(LoadError, match="количество полей"):
        read_source("crm_orders", data)


def test_duplicate_headers(valid_raw):
    data = valid_raw["crm_orders"].to_csv(index=False).replace("client_name", "order_id").encode()
    with pytest.raises(LoadError, match="повторяющиеся"):
        read_source("crm_orders", data)


def test_upload_size_limits(monkeypatch, files):
    monkeypatch.setattr("src.loader.MAX_FILE_BYTES", 5)
    with pytest.raises(LoadError, match="размер"):
        read_source("crm_orders", files["crm_orders.csv"])
    with pytest.raises(LoadError, match="размер"):
        load_zip(zip_bytes(files))


def test_zip_size_limit(monkeypatch, files):
    monkeypatch.setattr("src.loader.MAX_ZIP_BYTES", 5)
    with pytest.raises(LoadError, match="ZIP превышает"):
        load_zip(zip_bytes(files))


def test_expanded_archive_limit(monkeypatch, files):
    monkeypatch.setattr("src.loader.MAX_TOTAL_BYTES", 5)
    with pytest.raises(LoadError, match="распакованный размер"):
        load_zip(zip_bytes(files))


def test_xlsx_expansion_limit(monkeypatch, files):
    monkeypatch.setattr("src.loader.MAX_XLSX_EXPANDED", 5)
    with pytest.raises(LoadError, match="распакованный размер"):
        read_source("production", files["production.xlsx"])


def test_row_limit(monkeypatch, files):
    monkeypatch.setattr("src.loader.MAX_ROWS", 2)
    with pytest.raises(LoadError, match="строк"):
        read_source("crm_orders", files["crm_orders.csv"])
    with pytest.raises(LoadError, match="строк"):
        read_source("production", files["production.xlsx"])
