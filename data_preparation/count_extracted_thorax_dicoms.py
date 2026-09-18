"""Count extracted thorax DICOM files for each spreadsheet scan_dir.

Run this script through RUN_THORAX_DICOM_COUNT_CHECK.bat on the Windows VM.
It reads the thorax link CSV, counts files in Z:\thorax_data, and writes a
new CSV with count_DICUM plus a readable text report.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath


DEFAULT_SPREADSHEET = Path(r"Z:\sectra2\spreadsheet\ThoraxLinktoScan_csv.csv")
DEFAULT_DATA_ROOT = Path(r"Z:\thorax_data")
DEFAULT_OUTPUT_DIR = Path(r"Z:\thorax_data")
CONDITION_FOLDERS = {
    "pt": "pneumothorax",
    "pneumothorax": "pneumothorax",
    "ht": "haemothorax",
    "hemothorax": "haemothorax",
    "haemothorax": "haemothorax",
    "htpt_joint": "joint",
    "hpt_joint": "joint",
    "joint": "joint",
}
INVALID_SCAN_DIRS = {"", "#n/a", "n/a", "na", "error", "none", "null", "nan"}


def choose_spreadsheet() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askopenfilename(
            title="Select ThoraxLinktoScan CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        root.destroy()
        return Path(selected) if selected else None
    except Exception:
        return None


def normalise_column_name(name: object) -> str:
    column_name = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    if column_name == "thorax_conditoin":
        return "thorax_condition"
    return column_name


def normalise_scan_dir(value: object) -> PurePosixPath | None:
    text = str(value or "").strip().replace("\\", "/")
    if text.lower() in INVALID_SCAN_DIRS:
        return None

    parts = [part for part in text.split("/") if part and part not in {".", ".."}]
    if len(parts) < 2 or any(not re.fullmatch(r"\d+", part) for part in parts):
        return None
    return PurePosixPath(*parts)


def condition_folder(value: object) -> str | None:
    condition = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return CONDITION_FOLDERS.get(condition)


def read_csv_rows(csv_path: Path) -> tuple[list[str], list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("The CSV is empty or has no header row.")
        original_headers = list(reader.fieldnames)
        normalised_headers = [normalise_column_name(header) for header in original_headers]
        rows = []
        for row in reader:
            rows.append(
                {
                    normalise_column_name(key): value
                    for key, value in row.items()
                    if key is not None
                }
            )
    return original_headers, normalised_headers, rows


def output_headers(original_headers: list[str], normalised_headers: list[str]) -> list[str]:
    headers = list(original_headers)
    if "count_DICUM" in headers:
        return headers

    try:
        condition_index = normalised_headers.index("thorax_condition")
    except ValueError:
        try:
            condition_index = normalised_headers.index("scan_dir")
        except ValueError:
            condition_index = len(headers) - 1

    headers.insert(condition_index + 1, "count_DICUM")
    return headers


def count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.rglob("*") if path.is_file())


def count_for_row(row: dict[str, str], data_root: Path) -> tuple[int, str, Path | None]:
    raw_scan_dir = row.get("scan_dir")
    raw_condition = row.get("thorax_condition")
    scan_dir = normalise_scan_dir(raw_scan_dir)
    condition = condition_folder(raw_condition)

    if scan_dir is None:
        return 0, "invalid_scan_dir", None
    if condition is None:
        return 0, "invalid_thorax_condition", None

    folder = data_root / condition / Path(*scan_dir.parts)
    if not folder.is_dir():
        return 0, "missing_extracted_folder", folder

    count = count_files(folder)
    if count == 0:
        return count, "empty_folder", folder
    if count < 20:
        return count, "low_slide_count", folder
    return count, "ok", folder


def write_counted_csv(
    source_csv: Path,
    original_headers: list[str],
    normalised_headers: list[str],
    rows: list[dict[str, str]],
    counts: list[int],
    output_dir: Path,
) -> Path:
    output_path = output_dir / f"{source_csv.stem}_with_count_DICUM.csv"
    headers = output_headers(original_headers, normalised_headers)

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row, count in zip(rows, counts):
            output_row = {}
            for original_header in original_headers:
                output_row[original_header] = row.get(normalise_column_name(original_header), "")
            output_row["count_DICUM"] = count
            writer.writerow(output_row)

    return output_path


def write_report(
    output_dir: Path,
    source_csv: Path,
    data_root: Path,
    counted_csv: Path,
    status_counts: Counter,
    details: list[str],
) -> Path:
    report_path = output_dir / f"dicum_count_report_{datetime.now():%Y%m%d_%H%M%S}.txt"
    lines = [
        "Thorax extracted DICUM count report",
        f"Created: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Input CSV: {source_csv}",
        f"Data root: {data_root}",
        f"Output CSV: {counted_csv}",
        "",
        "Summary:",
    ]
    lines.extend(f"  {name}: {count}" for name, count in sorted(status_counts.items()))
    if details:
        lines.extend(["", "Rows needing review:"])
        lines.extend(f"  {detail}" for detail in details)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run_check(spreadsheet: Path, data_root: Path, output_dir: Path) -> int:
    if not spreadsheet.is_file():
        print(f"ERROR: CSV was not found: {spreadsheet}")
        selected = choose_spreadsheet()
        if selected is None:
            return 2
        spreadsheet = selected
    if not data_root.is_dir():
        print(f"ERROR: Extracted thorax data folder was not found: {data_root}")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    original_headers, normalised_headers, rows = read_csv_rows(spreadsheet)
    required_columns = {"scan_dir", "thorax_condition"}
    missing_columns = required_columns.difference(normalised_headers)
    if missing_columns:
        print("ERROR: CSV is missing required column(s): " + ", ".join(sorted(missing_columns)))
        print("Detected columns: " + ", ".join(original_headers))
        return 2

    counts: list[int] = []
    status_counts: Counter = Counter()
    details: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        count, status, folder = count_for_row(row, data_root)
        counts.append(count)
        status_counts[status] += 1
        if status != "ok":
            folder_text = f" folder={folder}" if folder is not None else ""
            details.append(
                f"Row {row_number}: {status}; scan_dir={row.get('scan_dir')!r}; "
                f"thorax_condition={row.get('thorax_condition')!r}; count_DICUM={count}{folder_text}"
            )

    counted_csv = write_counted_csv(
        spreadsheet,
        original_headers,
        normalised_headers,
        rows,
        counts,
        output_dir,
    )
    report_path = write_report(
        output_dir,
        spreadsheet,
        data_root,
        counted_csv,
        status_counts,
        details,
    )

    print("\nFinished.")
    print(f"Rows checked: {len(rows)}")
    print(f"New CSV: {counted_csv}")
    print(f"Report: {report_path}")
    print("Summary: " + ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count extracted thorax DICUM files by scan_dir.")
    parser.add_argument("spreadsheet", nargs="?", type=Path, default=DEFAULT_SPREADSHEET)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help=r"Extracted data root (default: Z:\thorax_data).")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help=r"Report/output folder (default: Z:\thorax_data).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_check(args.spreadsheet, args.data_root, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
