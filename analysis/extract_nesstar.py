"""Export the TUS 2024 Nesstar datasets to compressed CSV using verified DDI metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR_ROOT))

from nesstar_reader.binary import parse_nesstar_binary
from nesstar_reader.exporter import _write_dataset_csv


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ddi_datasets(path: Path) -> dict[int, dict[str, object]]:
    root = ET.parse(path).getroot()
    datasets: dict[int, dict[str, object]] = {}
    elements = list(root.iter())
    variables = [element for element in elements if element.tag.rsplit("}", 1)[-1] == "var"]

    file_descriptions = [
        element for element in elements
        if element.tag.rsplit("}", 1)[-1] == "fileDscr"
    ]
    for file_description in file_descriptions:
        file_id = file_description.get("ID", "")
        if not file_id.startswith("F") or not file_id[1:].isdigit():
            continue
        dataset_number = int(file_id[1:])
        descendants = list(file_description.iter())
        file_name = next(
            (
                (element.text or "").strip()
                for element in descendants
                if element.tag.rsplit("}", 1)[-1] == "fileName"
            ),
            "",
        )
        case_text = next(
            (
                (element.text or "").strip()
                for element in descendants
                if element.tag.rsplit("}", 1)[-1] == "caseQnty"
            ),
            "",
        )
        if not file_name or not case_text.isdigit():
            continue

        dataset_variables = [
            variable
            for variable in variables
            if file_id in (variable.get("files") or "").split()
        ]
        dataset_variables.sort(
            key=lambda variable: int((variable.get("ID") or "V0")[1:])
        )
        datasets[dataset_number] = {
            "file_name": file_name,
            "record_count": int(case_text),
            "variables": [variable.get("name", "") for variable in dataset_variables],
        }

    return datasets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw" / "TUS2024.Nesstar",
    )
    parser.add_argument(
        "--ddi",
        type=Path,
        default=PROJECT_ROOT / "docs" / "TUS2024_DDI.xml",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw" / "records",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace exports previously created by this script.",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    ddi_path = args.ddi.resolve()
    output_dir = args.output_dir.resolve()
    if not source.is_file() or not ddi_path.is_file():
        raise FileNotFoundError("The Nesstar source and official DDI XML must both exist.")

    parsed = parse_nesstar_binary(source)
    ddi = ddi_datasets(ddi_path)
    descriptors = {dataset.dataset_number: dataset for dataset in parsed.datasets}

    if set(descriptors) != set(ddi):
        raise ValueError(
            f"Dataset IDs differ: container={sorted(descriptors)}, DDI={sorted(ddi)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    data = source.read_bytes()
    for dataset_number, descriptor in sorted(descriptors.items()):
        contract = ddi[dataset_number]
        expected_variables = contract["variables"]
        actual_variables = [variable.name for variable in descriptor.variables]
        if descriptor.row_count != contract["record_count"]:
            raise ValueError(
                f"Dataset F{dataset_number} row count differs from the official DDI: "
                f"{descriptor.row_count} != {contract['record_count']}"
            )
        if actual_variables != expected_variables:
            raise ValueError(
                f"Dataset F{dataset_number} variable order differs from the official DDI."
            )

        dataset_name = Path(str(contract["file_name"])).stem
        final_data = output_dir / f"{dataset_name}.csv.gz"
        final_metadata = output_dir / f"{dataset_name}.json"
        if (final_data.exists() or final_metadata.exists()) and not args.overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing export for {dataset_name}."
            )
        temp_data = output_dir / f".{dataset_name}.partial.csv.gz"
        temp_metadata = output_dir / f".{dataset_name}.partial.json"
        if temp_data.exists() or temp_metadata.exists():
            raise FileExistsError(
                f"An incomplete prior export exists for {dataset_name}; inspect it before rerunning."
            )

        metadata = {
            "source": str(source),
            "source_sha256": source_hash,
            "parser": {
                "repository": "https://github.com/jmoy/nesstar-reader",
                "commit": "2c1bbb50de22835f3179838ef05bfe9b450ebaf5",
                "license": "MIT",
                "format_notes_are_unofficial": True,
            },
            "dataset": {
                "file_id": f"F{dataset_number}",
                "file_name": contract["file_name"],
                "record_count": descriptor.row_count,
                "variable_count": descriptor.variable_count,
            },
            "variables": [
                {
                    "name": variable.name,
                    "storage_mode": variable.mode_code,
                    "width_value": variable.width_value,
                    "value_format_code": variable.value_format_code,
                    "value_family": variable.value_family,
                    "missing_value_code": variable.missing_value_code,
                    "additive_offset": variable.additive_offset,
                }
                for variable in descriptor.variables
            ],
        }
        metadata_path = temp_metadata
        _write_dataset_csv(
            temp_data,
            data,
            descriptor,
            chunk_size=10_000,
            delimiter=",",
            include_header=True,
        )
        with metadata_path.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(metadata, target, ensure_ascii=False, indent=2)
            target.write("\n")
        temp_data.replace(final_data)
        metadata_path.replace(final_metadata)
        print(
            f"Exported F{dataset_number} {dataset_name}: "
            f"{descriptor.row_count:,} records, {descriptor.variable_count} variables"
        )


if __name__ == "__main__":
    main()
