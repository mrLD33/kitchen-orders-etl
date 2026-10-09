"""Generate the committed synthetic fixtures; never overwrite supplied demo files."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.loader import FILENAMES, SCHEMAS


def generate(destination):
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / name).exists() for name in FILENAMES.values()):
        raise SystemExit("Демонстрационные файлы уже существуют; перезапись запрещена.")
    # Fixed fixture dates; tests always evaluate as of 2026-10-01.
    rows = {
        "crm_orders": [
            ["001", "Анна Смирнова", "Салон А", "Ирина", "2026-09-01", "2026-09-20", "200000", "completed"],
            ["002", "Борис Петров", "Салон А", "Ирина", "2026-09-02", "2026-09-25", "150000", "completed"],
            ["003", "Вера Орлова", "Салон Б", "Олег", "2026-09-03", "2026-10-15", "100000", "production"],
            ["004", "Глеб Иванов", "Салон Б", "Олег", "2026-09-04", "2026-09-28", "120000", "production"],
            ["005", "Дарья Волкова", "Салон А", "Ирина", "31.02.2026", "2026-10-20", "0", "new"],
            ["006", "Елена Соколова", "Салон Б", "Олег", "2026-09-05", "2026-10-20", "80000", "design"],
        ],
        "measurements": [
            ["001", "Алексей", "2026-09-03", "2026-09-03", "completed", "да", "да"],
            ["002", "Алексей", "2026-09-05", "2026-09-07", "completed", "да", "да"],
            ["003", "Михаил", "2026-09-06", "2026-09-06", "completed", "да", "да"],
            ["004", "Михаил", "2026-09-07", "2026-09-07", "completed", "да", "да"],
            ["005", "Алексей", "2026-10-10", None, "planned", "нет", "нет"],
            ["006", "Михаил", "2026-09-08", "2026-09-08", "completed", "нет", "нет"],
            ["999", "Михаил", "2026-09-08", None, "planned", "нет", "нет"],
        ],
        "production": [
            ["001", "completed", "2026-09-05", "2026-09-15", "2026-09-15", 70000, 20000, 5000],
            ["002", "completed", "2026-09-08", "2026-09-20", None, 50000, 20000, 5000],
            ["003", "in_progress", "2026-09-07", "2026-10-10", None, 40000, 10000, 2000],
            ["003", "in_progress", "2026-09-07", "2026-10-12", None, 45000, 10000, 2000],
            ["004", "in_progress", "2026-09-08", "2026-09-22", None, -1000, 20000, 3000],
            ["006", "planned", None, "2026-10-12", None, 30000, None, 2000],
        ],
        "installation": [
            ["001", "Сергей", "2026-09-20", "2026-09-20", "completed", 10000, 5000, "нет"],
            ["002", "Сергей", "2026-09-25", None, "planned", 8000, 3000, "нет"],
            ["003", "Денис", "2026-10-15", None, "planned", 6000, 2000, "нет"],
            ["004", "Денис", "2026-09-28", None, "planned", 8000, 3000, "нет"],
            ["006", "Денис", "2026-10-20", None, "planned", 5000, 2000, "нет"],
            ["888", "Сергей", "2026-09-10", None, "planned", 5000, 2000, "нет"],
        ],
    }
    for source, records in rows.items():
        frame = pd.DataFrame(records, columns=SCHEMAS[source])
        path = destination / FILENAMES[source]
        if source == "crm_orders":
            frame.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            frame.to_excel(path, index=False, engine="openpyxl")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "demo_data")
