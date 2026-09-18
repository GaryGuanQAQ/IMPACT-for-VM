"""Convert extracted thorax DICOM studies into NRRD volumes.

Run through RUN_THORAX_DICOM_TO_NRRD.bat on the VM. Each readable study is
converted using its largest DICOM series, matching the original converter.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import SimpleITK as sitk


DEFAULT_INPUT_ROOT = Path(r"Z:\thorax_data")
DEFAULT_OUTPUT_ROOT = Path(r"Z:\thorax_nrrd")
CONDITIONS = ("pneumothorax", "haemothorax", "joint")


def find_study_directories(condition_root: Path) -> list[Path]:
    """Return every directory under a condition that holds a readable DICOM series."""
    studies: list[Path] = []
    for current_root, _, files in os.walk(condition_root):
        if not files:
            continue
        directory = Path(current_root)
        try:
            series_ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(str(directory)) or []
        except RuntimeError:
            series_ids = []
        if series_ids:
            studies.append(directory)
    return studies


def largest_series_files(study_dir: Path) -> list[str]:
    series_ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(str(study_dir)) or []
    if not series_ids:
        raise RuntimeError("No readable DICOM series found")

    series = [
        list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(study_dir), series_id))
        for series_id in series_ids
    ]
    files = max(series, key=len)
    if not files:
        raise RuntimeError("The readable DICOM series has no files")
    return files


def nrrd_path_for_study(output_root: Path, condition: str, condition_root: Path, study_dir: Path) -> Path:
    relative_study = study_dir.relative_to(condition_root)
    return output_root / condition / relative_study.parent / f"{relative_study.name}.nrrd"


def convert_study(study_dir: Path, destination: Path) -> tuple[tuple[int, int, int], tuple[float, float, float]]:
    files = largest_series_files(study_dir)
    reader = sitk.ImageSeriesReader()
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()
    reader.SetFileNames(files)
    image = reader.Execute()

    destination.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(destination), useCompression=True)
    return image.GetSize(), image.GetSpacing()


def write_report(output_root: Path, rows: list[dict[str, str]], counts: Counter) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"dicom_to_nrrd_report_{timestamp}.csv"
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("condition", "study_directory", "nrrd_file", "status", "details"))
        writer.writeheader()
        writer.writerows(rows)

    print("\nSummary: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    print(f"Report: {report_path}")
    return report_path


def convert_all(input_root: Path, output_root: Path, overwrite: bool) -> int:
    if not input_root.is_dir():
        print(f"ERROR: Input folder was not found: {input_root}")
        return 2

    counts: Counter = Counter()
    report_rows: list[dict[str, str]] = []
    for condition in CONDITIONS:
        condition_root = input_root / condition
        if not condition_root.is_dir():
            counts["missing_condition_folder"] += 1
            print(f"SKIPPED: Condition folder not found: {condition_root}")
            continue

        studies = find_study_directories(condition_root)
        print(f"{condition}: found {len(studies)} readable DICOM study folder(s).")
        for study_dir in studies:
            destination = nrrd_path_for_study(output_root, condition, condition_root, study_dir)
            if destination.exists() and not overwrite:
                counts["skipped_existing"] += 1
                report_rows.append(
                    {
                        "condition": condition,
                        "study_directory": str(study_dir),
                        "nrrd_file": str(destination),
                        "status": "skipped_existing",
                        "details": "Use --overwrite to convert it again.",
                    }
                )
                continue

            try:
                size, spacing = convert_study(study_dir, destination)
                counts[f"converted_{condition}"] += 1
                details = f"size={size}; spacing={spacing}"
                print(f"CONVERTED: {study_dir} -> {destination}")
                report_rows.append(
                    {
                        "condition": condition,
                        "study_directory": str(study_dir),
                        "nrrd_file": str(destination),
                        "status": "converted",
                        "details": details,
                    }
                )
            except Exception as error:  # Keep processing when one study is malformed.
                counts["failed"] += 1
                print(f"FAILED: {study_dir} ({error})")
                report_rows.append(
                    {
                        "condition": condition,
                        "study_directory": str(study_dir),
                        "nrrd_file": str(destination),
                        "status": "failed",
                        "details": str(error),
                    }
                )

    write_report(output_root, report_rows, counts)
    return 1 if counts["failed"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert extracted thorax DICOM folders into NRRD volumes.")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT, help=r"DICOM root (default: Z:\thorax_data).")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help=r"NRRD root (default: Z:\thorax_nrrd).")
    parser.add_argument("--overwrite", action="store_true", help="Replace NRRD files that already exist.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return convert_all(args.input_root, args.output_root, args.overwrite)


if __name__ == "__main__":
    sys.exit(main())
