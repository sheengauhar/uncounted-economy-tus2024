"""Validate, summarize, and chart the TUS 2024 uncounted-economy records."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import gzip
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
PERSON_FILE = ROOT / "data" / "raw" / "records" / "tus106PER.csv.gz"
HOUSEHOLD_FILE = ROOT / "data" / "raw" / "records" / "tus106HH.csv.gz"
LAYOUT_FILE = ROOT / "docs" / "Data_Layout_TUS_2024.xlsx"
PROCESSED = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
CHARTS = ROOT / "outputs" / "charts"

EXPECTED_PERSON_ROWS = 10_211_478
EXPECTED_HOUSEHOLD_ROWS = 139_489
TARGET_MIN_AGE = 6
GOODS_CODES = {"211", "212", "215", "241", "242"}
METRIC_CODES = {
    "goods_own_use": lambda code: code.startswith("2"),
    "domestic_services": lambda code: code.startswith("3"),
    "household_scope": lambda code: code.startswith(("2", "3")),
    "crops_211": lambda code: code == "211",
    "animals_212": lambda code: code == "212",
    "wild_products_215": lambda code: code == "215",
    "firewood_241": lambda code: code == "241",
    "water_242": lambda code: code == "242",
    "other_goods": lambda code: code.startswith("2") and code not in GOODS_CODES,
}
GROUP_METRICS = tuple(METRIC_CODES)
DISPLAY_NAME = {
    "goods_own_use": "Own-use goods production (ICATUS 2xx)",
    "domestic_services": "Unpaid domestic services (ICATUS 3xx)",
    "household_scope": "Household production and domestic services (2xx+3xx)",
    "crops_211": "Crops and kitchen gardens (211)",
    "animals_212": "Animal farming (212)",
    "wild_products_215": "Wild products (215)",
    "firewood_241": "Firewood and natural fuel (241)",
    "water_242": "Water collection (242)",
    "other_goods": "Other own-use goods production (2xx)",
}
GENDER_NAME = {"1": "Male", "2": "Female", "3": "Transgender"}
SECTOR_NAME = {"1": "Rural", "2": "Urban"}
LAND_LABELS = {
    "1": "<0.01 ha",
    "2": "0.01-0.05 ha",
    "3": "0.05-0.5 ha",
    "4": "0.5-1.0 ha",
    "5": "1.0-2.5 ha",
    "6": "2.5-5.0 ha",
    "7": "5.0-7.5 ha",
    "8": "7.5-10.0 ha",
    "10": "10.0-15.0 ha",
    "11": "15.0-20.0 ha",
    "12": "20.0 ha or more",
    "99": "Other/unknown code",
}
AGE_BANDS = (
    (6, 15, "6-14"),
    (15, 25, "15-24"),
    (25, 45, "25-44"),
    (45, 60, "45-59"),
    (60, 200, "60+"),
)
OVERWRITE = False


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def read_state_lookup(path: Path) -> dict[str, str]:
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as workbook:
        book = ET.fromstring(workbook.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            strings = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(text.text or "" for text in item.findall(".//m:t", ns))
                for item in strings.findall("m:si", ns)
            ]

        for sheet in book.findall("m:sheets/m:sheet", ns):
            if sheet.attrib.get("name", "").casefold() != "state code":
                continue
            target = targets[sheet.attrib[f"{{{ns['r']}}}id"]].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            data = ET.fromstring(workbook.read(target))
            result: dict[str, str] = {}
            for row in data.findall(".//m:sheetData/m:row", ns):
                cells: dict[str, str] = {}
                for cell in row.findall("m:c", ns):
                    reference = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
                    if reference is None:
                        continue
                    value_node = cell.find("m:v", ns)
                    value = "" if value_node is None else value_node.text or ""
                    if cell.attrib.get("t") == "s" and value:
                        value = shared_strings[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(
                            text.text or "" for text in cell.findall(".//m:t", ns)
                        )
                    cells[reference.group(0)] = value.strip()
                code, name = cells.get("A", ""), cells.get("B", "")
                if code.isdigit() and name:
                    result[code.zfill(2)] = name
            if result:
                # Normalize historic spellings in the source workbook for display.
                result.update(
                    {
                        "25": "Dadra and Nagar Haveli and Daman and Diu",
                        "33": "Tamil Nadu",
                        "34": "Puducherry",
                        "35": "Andaman and Nicobar Islands",
                    }
                )
                return result
    raise ValueError(f"No state-code table found in {path}")


def parse_int(value: str) -> int | None:
    if not value or value.strip().casefold() in {"sysmiss", "na", "nan"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_time(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if minute not in {0, 30} or hour > 24 or (hour == 24 and minute != 0):
        return None
    return hour * 60 + minute


def age_band(age: int) -> str:
    for lower, upper, label in AGE_BANDS:
        if lower <= age < upper:
            return label
    return "Unknown"


def csv_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        header = next(csv.reader(source))

    def rows():
        with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            next(reader)
            yield from reader

    return header, rows()


def read_households(state_lookup: dict[str, str]) -> tuple[dict[tuple[str, str], dict[str, str]], dict]:
    households: dict[tuple[str, str], dict[str, str]] = {}
    diagnostics = Counter()
    fsu_weights: dict[str, int] = {}
    header, rows = csv_rows(HOUSEHOLD_FILE)
    index = {name: position for position, name in enumerate(header)}
    for row in rows:
        diagnostics["rows"] += 1
        key = (row[index["FSU_Serial_No"]], row[index["Sample_HH_No"]])
        if key in households:
            diagnostics["duplicate_household_keys"] += 1
            continue
        weight_raw = parse_int(row[index["MULT"]])
        region = row[index["NSS_Region"]]
        state_code = region[:2] if len(region) >= 2 else ""
        record = {
            "FSU_Serial_No": key[0],
            "Sample_HH_No": key[1],
            "Sector": row[index["Sector"]],
            "NSS_Region": region,
            "State_Code": state_code,
            "State_Name": state_lookup.get(state_code, ""),
            "Household_Size": row[index["Household_Size"]],
            "Land_Possessed": row[index["Land_Possessed"]],
            "Energy_Cooking": row[index["Energy_Cooking"]],
            "Exp_Imputed_B": row[index["Exp_Imputed_B"]],
            "MULT": row[index["MULT"]],
            "Weight_Raw": weight_raw,
            "Weight": (weight_raw / 100) if weight_raw is not None else None,
        }
        households[key] = record
        if weight_raw is None:
            diagnostics["missing_household_weights"] += 1
        if state_code not in state_lookup:
            diagnostics["unmapped_household_state"] += 1
        if weight_raw is not None:
            previous = fsu_weights.setdefault(key[0], weight_raw)
            if previous != weight_raw:
                diagnostics["fsu_weight_variations"] += 1

    diagnostics["unique_household_keys"] = len(households)
    diagnostics["fsu_count"] = len(fsu_weights)
    if diagnostics["rows"] != EXPECTED_HOUSEHOLD_ROWS:
        raise ValueError(
            f"Household row count {diagnostics['rows']} does not match DDI "
            f"count {EXPECTED_HOUSEHOLD_ROWS}"
        )
    if diagnostics["duplicate_household_keys"]:
        raise ValueError("Duplicate household identifiers found.")
    diagnostics["weight_sum"] = sum(
        record["Weight"] or 0 for record in households.values()
    )
    return households, dict(diagnostics)


def new_person_state(row: list[str], index: dict[str, int]) -> dict:
    fields = (
        "FSU_Serial_No",
        "Sample_HH_No",
        "Person_Serial_No",
        "Gender",
        "Age",
        "Sector",
        "NSS_Region",
        "Stratum",
        "Sub_Stratum",
        "NSC",
        "MULT",
        "Day_of_Week",
        "Type_of_Day",
        "Response_Code",
    )
    values = {name: row[index[name]] for name in fields}
    values["activities"] = []
    return values


def activity_row(row: list[str], index: dict[str, int]) -> dict[str, str]:
    fields = (
        "Activity_Member_Serial_No",
        "Activity_Serial_No",
        "Time_From",
        "Time_To",
        "Multiple_Activity_Flag",
        "Simultaneous_Activity_Flag",
        "Major_Activity_Flag",
        "Activity_Code_3Digit",
        "Unpaid_Paid_Status",
    )
    return {name: row[index[name]] for name in fields}


def analyze_person(
    person: dict,
    households: dict[tuple[str, str], dict[str, str]],
    state_lookup: dict[str, str],
    diagnostics: Counter,
    domains: dict[str, Counter],
) -> tuple[dict[str, str] | None, dict | None]:
    age = parse_int(person["Age"])
    if age is None:
        diagnostics["persons_missing_age"] += 1
        return None, None
    if age < TARGET_MIN_AGE:
        diagnostics["roster_person_under_6"] += 1
        if person["activities"]:
            diagnostics["under_6_with_activity_rows"] += 1
        return None, None

    activities = person["activities"]
    if not activities:
        diagnostics["persons_6plus_without_diary"] += 1
        return None, None

    weight_raw = parse_int(person["MULT"])
    gender = person["Gender"]
    sector = person["Sector"]
    region = person["NSS_Region"]
    state_code = region[:2] if len(region) >= 2 else ""
    state_name = state_lookup.get(state_code, "")
    if weight_raw is None or gender not in GENDER_NAME or sector not in SECTOR_NAME or not state_name:
        diagnostics["persons_excluded_missing_core_values"] += 1
        return None, None
    response_code = person["Response_Code"]
    if response_code not in {"1", "2"}:
        diagnostics["persons_invalid_response_code"] += 1
        return None, None
    diagnostics[f"diary_response_code_{response_code}"] += 1

    household_key = (person["FSU_Serial_No"], person["Sample_HH_No"])
    household = households.get(household_key)
    if household is None:
        diagnostics["person_household_unmatched"] += 1
        household = {}
    else:
        if household["Weight_Raw"] != weight_raw:
            diagnostics["person_household_weight_mismatch"] += 1
        if household["Sector"] != sector or household["State_Code"] != state_code:
            diagnostics["person_household_geography_mismatch"] += 1

    weight = weight_raw / 100
    slots: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    valid_activity_rows = 0
    for activity in activities:
        code = activity["Activity_Code_3Digit"]
        start = parse_time(activity["Time_From"])
        end = parse_time(activity["Time_To"])
        if not code or len(code) != 3 or not code.isdigit():
            diagnostics["invalid_or_missing_activity_code"] += 1
            continue
        if start is None or end is None or start == 1440:
            diagnostics["invalid_activity_time"] += 1
            continue
        if end < start:
            end += 1440
            diagnostics["midnight_crossing_intervals"] += 1
        duration = end - start
        if duration <= 0 or duration > 1440:
            diagnostics["invalid_activity_duration"] += 1
            continue
        valid_activity_rows += 1
        slots[(start, end)].append(activity)
        domains["activity_code"][code] += 1
        domains["unpaid_paid_status"][activity["Unpaid_Paid_Status"]] += 1
        domains["multiple_activity_flag"][activity["Multiple_Activity_Flag"]] += 1
        domains["simultaneous_activity_flag"][activity["Simultaneous_Activity_Flag"]] += 1
        domains["major_activity_flag"][activity["Major_Activity_Flag"]] += 1
        if activity["Activity_Member_Serial_No"] != person["Person_Serial_No"]:
            diagnostics["activity_member_not_diary_person"] += 1
        if code.startswith("2") and activity["Unpaid_Paid_Status"] != "04":
            diagnostics["goods_status_not_04"] += 1
        if code.startswith("3") and activity["Unpaid_Paid_Status"] != "03":
            diagnostics["domestic_status_not_03"] += 1

    if valid_activity_rows == 0:
        diagnostics["persons_6plus_without_valid_activity"] += 1
        return None, None

    minutes = {name: 0.0 for name in GROUP_METRICS}
    major_minutes = {name: 0.0 for name in GROUP_METRICS}
    participation = {name: False for name in GROUP_METRICS}
    major_participation = {name: False for name in GROUP_METRICS}
    slot_duration_total = 0
    slot_union_total = 0
    duplicate_activity_serials = 0
    multi_slots = 0
    major_slot_errors = 0
    multiple_flag_errors = 0
    all_major_flags_valid = True
    seen_serials: set[str] = set()

    intervals: list[tuple[int, int]] = []
    for (start, end), slot_rows in slots.items():
        duration = end - start
        slot_duration_total += duration
        intervals.append((start, end))
        if len(slot_rows) > 1:
            multi_slots += 1

        unique_rows: list[dict[str, str]] = []
        serials_in_slot: set[str] = set()
        for activity in slot_rows:
            serial = activity["Activity_Serial_No"]
            if serial and serial in serials_in_slot:
                duplicate_activity_serials += 1
                continue
            if serial:
                serials_in_slot.add(serial)
            unique_rows.append(activity)

        if len(unique_rows) != len(slot_rows):
            diagnostics["duplicate_activity_serial_rows"] += len(slot_rows) - len(unique_rows)
        if not unique_rows:
            continue

        major_rows = [activity for activity in unique_rows if activity["Major_Activity_Flag"] == "1"]
        if len(major_rows) != 1:
            major_slot_errors += 1
            all_major_flags_valid = False
        expected_multiple = "1" if len(unique_rows) > 1 else "2"
        if any(activity["Multiple_Activity_Flag"] != expected_multiple for activity in unique_rows):
            multiple_flag_errors += 1

        slot_share = duration / len(unique_rows)
        for activity in unique_rows:
            code = activity["Activity_Code_3Digit"]
            is_major = activity["Major_Activity_Flag"] == "1"
            for metric, matcher in METRIC_CODES.items():
                if not matcher(code):
                    continue
                participation[metric] = True
                minutes[metric] += slot_share
                if is_major:
                    major_participation[metric] = True
                    major_minutes[metric] += duration

    # Union of recorded intervals detects non-identical overlaps that would
    # otherwise make a naive duration sum exceed the 24-hour diary.
    split_intervals: list[tuple[int, int]] = []
    for start, end in intervals:
        if end <= 1440:
            split_intervals.append((start, end))
        else:
            split_intervals.extend(((start, 1440), (0, end - 1440)))
    merged: list[list[int]] = []
    for start, end in sorted(split_intervals):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    slot_union_total = sum(end - start for start, end in merged)
    if slot_duration_total > slot_union_total:
        diagnostics["nonidentical_overlapping_intervals"] += 1
    if slot_duration_total != 1440:
        diagnostics["diaries_slot_duration_not_1440"] += 1
    if slot_union_total != 1440:
        diagnostics["diaries_union_duration_not_1440"] += 1
    diagnostics["diaries_with_multiple_activity_slots"] += multi_slots > 0
    diagnostics["diaries_with_invalid_major_flag_slots"] += not all_major_flags_valid
    diagnostics["diaries_with_duplicate_activity_serials"] += duplicate_activity_serials > 0
    diagnostics["major_activity_flag_slot_errors"] += major_slot_errors
    diagnostics["multiple_activity_flag_slot_errors"] += multiple_flag_errors

    summary = {
        **{key: person[key] for key in (
            "FSU_Serial_No",
            "Sample_HH_No",
            "Person_Serial_No",
            "Gender",
            "Sector",
            "NSS_Region",
            "Stratum",
            "Sub_Stratum",
            "NSC",
            "Day_of_Week",
            "Type_of_Day",
            "Response_Code",
        )},
        "State_Code": state_code,
        "State_Name": state_name,
        "Age": str(age),
        "Age_Group": age_band(age),
        "MULT": person["MULT"],
        "Weight_Raw": str(weight_raw),
        "Weight": f"{weight:.2f}",
        "Household_Size": household.get("Household_Size", ""),
        "Land_Possessed": household.get("Land_Possessed", ""),
        "Energy_Cooking": household.get("Energy_Cooking", ""),
        "Exp_Imputed_B": household.get("Exp_Imputed_B", ""),
        "Activity_Rows": str(len(activities)),
        "Valid_Activity_Rows": str(valid_activity_rows),
        "Unique_Time_Slots": str(len(slots)),
        "Slot_Duration_Minutes": str(slot_duration_total),
        "Union_Duration_Minutes": str(slot_union_total),
        "Duplicate_Activity_Serials": str(duplicate_activity_serials),
    }
    for metric in GROUP_METRICS:
        summary[f"Equal_Minutes_{metric}"] = f"{minutes[metric]:.6f}"
        summary[f"Major_Minutes_{metric}"] = f"{major_minutes[metric]:.6f}"
        summary[f"Participates_{metric}"] = "1" if participation[metric] else "0"
        summary[f"Major_Participates_{metric}"] = (
            "1" if major_participation[metric] else "0"
        )

    household_update = {
        "scope_minutes": minutes["household_scope"],
        "goods_minutes": minutes["goods_own_use"],
        "domestic_minutes": minutes["domestic_services"],
        "water_minutes": minutes["water_242"],
        "firewood_minutes": minutes["firewood_241"],
        "any_scope": participation["household_scope"],
        "any_goods": participation["goods_own_use"],
        "any_domestic": participation["domestic_services"],
        "any_water": participation["water_242"],
        "any_firewood": participation["firewood_241"],
    }
    diagnostics["valid_person_days"] += 1
    diagnostics["valid_activity_rows"] += valid_activity_rows
    diagnostics["person_weight_sum"] += weight
    diagnostics["activity_rows_weighted"] += len(activities)
    return summary, household_update


def write_person_summaries(
    households: dict[tuple[str, str], dict[str, str]],
    state_lookup: dict[str, str],
) -> tuple[dict[tuple[str, str], dict[str, float]], dict, dict[str, Counter]]:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    diagnostics = Counter()
    domains = defaultdict(Counter)
    household_aggregates: dict[tuple[str, str], dict[str, float]] = {}
    output_path = PROCESSED / "person_day_summary.csv.gz"
    temp_output_path = PROCESSED / ".person_day_summary.partial.csv.gz"
    if (output_path.exists() and not OVERWRITE) or temp_output_path.exists():
        raise FileExistsError(f"Refusing to overwrite {output_path}")

    header, rows = csv_rows(PERSON_FILE)
    index = {name: position for position, name in enumerate(header)}
    output_fields = [
        "FSU_Serial_No", "Sample_HH_No", "Person_Serial_No", "Gender",
        "Sector", "NSS_Region", "Stratum", "Sub_Stratum", "NSC",
        "Day_of_Week", "Type_of_Day", "Response_Code", "State_Code",
        "State_Name", "Age", "Age_Group", "MULT", "Weight_Raw", "Weight",
        "Household_Size", "Land_Possessed", "Energy_Cooking", "Exp_Imputed_B",
        "Activity_Rows", "Valid_Activity_Rows", "Unique_Time_Slots",
        "Slot_Duration_Minutes", "Union_Duration_Minutes",
        "Duplicate_Activity_Serials",
    ]
    for metric in GROUP_METRICS:
        output_fields.extend(
            (
                f"Equal_Minutes_{metric}",
                f"Major_Minutes_{metric}",
                f"Participates_{metric}",
                f"Major_Participates_{metric}",
            )
        )

    seen_person_keys: set[tuple[str, str, str]] = set()
    current: dict | None = None
    current_key: tuple[str, str, str] | None = None
    with gzip.open(temp_output_path, "wt", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=output_fields, lineterminator="\n")
        writer.writeheader()

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            summary, household_update = analyze_person(
                current, households, state_lookup, diagnostics, domains
            )
            if summary is not None:
                writer.writerow(summary)
                key = (summary["FSU_Serial_No"], summary["Sample_HH_No"])
                totals = household_aggregates.setdefault(
                    key,
                    {
                        "diary_people": 0.0,
                        "scope_minutes": 0.0,
                        "goods_minutes": 0.0,
                        "domestic_minutes": 0.0,
                        "water_minutes": 0.0,
                        "firewood_minutes": 0.0,
                        "any_scope": 0.0,
                        "any_goods": 0.0,
                        "any_domestic": 0.0,
                        "any_water": 0.0,
                        "any_firewood": 0.0,
                    },
                )
                totals["diary_people"] += 1
                for name, value in household_update.items():
                    if name.startswith("any_"):
                        totals[name] = max(totals[name], float(value))
                    else:
                        totals[name] += value
            current = None

        for row in rows:
            diagnostics["person_file_rows"] += 1
            key = (
                row[index["FSU_Serial_No"]],
                row[index["Sample_HH_No"]],
                row[index["Person_Serial_No"]],
            )
            if key != current_key:
                flush()
                if key in seen_person_keys:
                    diagnostics["noncontiguous_person_groups"] += 1
                    raise ValueError(
                        f"Person key reappeared out of sequence: {key!r}"
                    )
                seen_person_keys.add(key)
                diagnostics["person_groups"] += 1
                current_key = key
                current = new_person_state(row, index)
            else:
                assert current is not None
                for field in (
                    "Gender", "Age", "Sector", "NSS_Region", "MULT",
                    "Day_of_Week", "Type_of_Day", "Response_Code",
                ):
                    if row[index[field]] != current[field]:
                        diagnostics["inconsistent_person_fields"] += 1
                        raise ValueError(
                            f"Person-level field {field} varies within {key!r}"
                        )
            assert current is not None
            serial = row[index["Activity_Serial_No"]]
            if serial:
                current["activities"].append(activity_row(row, index))
            else:
                diagnostics["rows_without_activity_serial"] += 1
        flush()

    if diagnostics["person_file_rows"] != EXPECTED_PERSON_ROWS:
        raise ValueError(
            f"Person row count {diagnostics['person_file_rows']} does not match DDI "
            f"count {EXPECTED_PERSON_ROWS}"
        )
    temp_output_path.replace(output_path)
    diagnostics["processed_person_days"] = diagnostics["valid_person_days"]
    diagnostics["expected_person_file_rows"] = EXPECTED_PERSON_ROWS
    diagnostics["person_file_row_count_matches_ddi"] = (
        diagnostics["person_file_rows"] == EXPECTED_PERSON_ROWS
    )
    diagnostics["unique_person_groups"] = diagnostics["person_groups"]
    diagnostics["noncontiguous_person_groups"] += 0
    return household_aggregates, dict(diagnostics), domains


def write_processed_households(households: dict[tuple[str, str], dict[str, str]]) -> None:
    output = PROCESSED / "household_characteristics.csv.gz"
    temp = PROCESSED / ".household_characteristics.partial.csv.gz"
    if (output.exists() and not OVERWRITE) or temp.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    fields = [
        "FSU_Serial_No",
        "Sample_HH_No",
        "Sector",
        "NSS_Region",
        "State_Code",
        "State_Name",
        "Household_Size",
        "Land_Possessed",
        "Energy_Cooking",
        "Exp_Imputed_B",
        "MULT",
        "Weight_Raw",
        "Weight",
    ]
    with gzip.open(temp, "wt", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for key in sorted(households):
            writer.writerow(households[key])
    temp.replace(output)


class MetricAccumulator:
    __slots__ = (
        "n",
        "weight",
        "weighted_minutes",
        "participants",
        "participant_weight",
        "participant_weighted_minutes",
    )

    def __init__(self) -> None:
        self.n = 0
        self.weight = 0.0
        self.weighted_minutes = 0.0
        self.participants = 0
        self.participant_weight = 0.0
        self.participant_weighted_minutes = 0.0

    def add(self, weight: float, minutes: float, participates: bool) -> None:
        self.n += 1
        self.weight += weight
        self.weighted_minutes += weight * minutes
        if participates:
            self.participants += 1
            self.participant_weight += weight
            self.participant_weighted_minutes += weight * minutes


def aggregate_person_metrics(
    treatment: str = "equal",
) -> dict[tuple[str, ...], dict[str, MetricAccumulator]]:
    if treatment not in {"equal", "major"}:
        raise ValueError(f"Unknown time-allocation treatment: {treatment}")
    minute_prefix = "Equal" if treatment == "equal" else "Major"
    participation_prefix = "Participates" if treatment == "equal" else "Major_Participates"
    groups: dict[tuple[str, ...], dict[str, MetricAccumulator]] = {}
    with gzip.open(
        PROCESSED / "person_day_summary.csv.gz",
        "rt",
        encoding="utf-8",
        newline="",
    ) as source:
        for row in csv.DictReader(source):
            gender = row["Gender"]
            sector = row["Sector"]
            state = row["State_Code"]
            age = row["Age_Group"]
            weight = float(row["Weight"])
            group_keys = [
                ("all", "India"),
                ("sector_gender", sector, gender),
                ("sector", sector),
                ("gender", gender),
                ("state", state),
                ("state_gender", state, gender),
                ("state_sector_gender", state, sector, gender),
                ("age_sector_gender", age, sector, gender),
            ]
            for group_key in group_keys:
                metric_map = groups.setdefault(group_key, {})
                for metric in GROUP_METRICS:
                    acc = metric_map.setdefault(metric, MetricAccumulator())
                    minutes = float(row[f"{minute_prefix}_Minutes_{metric}"])
                    participates = row[f"{participation_prefix}_{metric}"] == "1"
                    acc.add(weight, minutes, participates)
    return groups


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.partial")
    if (path.exists() and not OVERWRITE) or temp.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    with temp.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.partial")
    if (path.exists() and not OVERWRITE) or temp.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def metric_output(
    group_key: tuple[str, ...],
    metric: str,
    acc: MetricAccumulator,
    state_lookup: dict[str, str],
) -> dict[str, str | int | float]:
    group_type, *values = group_key
    result: dict[str, str | int | float] = {
        "group_type": group_type,
        "group_code": "|".join(values),
        "metric": metric,
        "metric_label": DISPLAY_NAME[metric],
        "sample_n": acc.n,
        "weighted_population": acc.weight,
        "participants_n": acc.participants,
        "weighted_participants": acc.participant_weight,
        "weighted_minutes_total": acc.weighted_minutes,
        "participation_rate_pct": (
            100 * acc.participant_weight / acc.weight if acc.weight else 0
        ),
        "mean_minutes_per_person": (
            acc.weighted_minutes / acc.weight if acc.weight else 0
        ),
        "mean_minutes_per_participant": (
            acc.participant_weighted_minutes / acc.participant_weight
            if acc.participant_weight
            else 0
        ),
    }
    if group_type in {"state", "state_gender", "state_sector_gender"}:
        result["state_code"] = values[0]
        result["state_name"] = state_lookup.get(values[0], "")
    if group_type == "sector_gender":
        result["sector"] = values[0]
        result["sector_name"] = SECTOR_NAME.get(values[0], "")
        result["gender"] = values[1]
        result["gender_name"] = GENDER_NAME.get(values[1], "")
    if group_type == "state_gender":
        result["gender"] = values[1]
        result["gender_name"] = GENDER_NAME.get(values[1], "")
    if group_type == "state_sector_gender":
        result["sector"] = values[1]
        result["sector_name"] = SECTOR_NAME.get(values[1], "")
        result["gender"] = values[2]
        result["gender_name"] = GENDER_NAME.get(values[2], "")
    if group_type == "age_sector_gender":
        result["age_group"] = values[0]
        result["sector"] = values[1]
        result["sector_name"] = SECTOR_NAME.get(values[1], "")
        result["gender"] = values[2]
        result["gender_name"] = GENDER_NAME.get(values[2], "")
    return result


def write_metric_tables(
    groups: dict[tuple[str, ...], dict[str, MetricAccumulator]],
    state_lookup: dict[str, str],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    all_rows = [
        metric_output(key, metric, accumulator, state_lookup)
        for key, metrics in groups.items()
        for metric, accumulator in metrics.items()
    ]
    group_rows = [row for row in all_rows if row["group_type"] == "sector_gender"]
    activity_rows = [
        row
        for row in all_rows
        if row["group_type"] == "all" and row["metric"] not in {
            "goods_own_use", "household_scope"
        }
    ]
    all_fields = list(dict.fromkeys(key for row in all_rows for key in row))
    write_csv(TABLES / "person_group_estimates.csv", all_fields, all_rows)
    write_csv(TABLES / "gender_rural_urban.csv", list(group_rows[0].keys()), group_rows)
    write_csv(TABLES / "activity_estimates.csv", list(activity_rows[0].keys()), activity_rows)

    state_rows = [
        row for row in all_rows
        if row["group_type"] == "state" and row["metric"] == "household_scope"
    ]
    state_rows.sort(key=lambda row: str(row["state_code"]))
    write_csv(TABLES / "state_comparison.csv", list(state_rows[0].keys()), state_rows)

    gender_lookup = {
        (row["state_code"], row["gender"]): row
        for row in all_rows
        if row["group_type"] == "state_gender"
        and row["metric"] == "household_scope"
        and row["gender"] in {"1", "2"}
    }
    gap_rows = []
    for state_code, state_name in state_lookup.items():
        male = gender_lookup.get((state_code, "1"))
        female = gender_lookup.get((state_code, "2"))
        if male is None or female is None:
            continue
        gap_rows.append(
            {
                "state_code": state_code,
                "state_name": state_name,
                "male_sample_n": male["sample_n"],
                "female_sample_n": female["sample_n"],
                "male_weighted_population": male["weighted_population"],
                "female_weighted_population": female["weighted_population"],
                "male_participation_pct": male["participation_rate_pct"],
                "female_participation_pct": female["participation_rate_pct"],
                "participation_gap_female_minus_male_pp": (
                    female["participation_rate_pct"] - male["participation_rate_pct"]
                ),
                "male_mean_minutes_per_person": male["mean_minutes_per_person"],
                "female_mean_minutes_per_person": female["mean_minutes_per_person"],
                "time_gap_female_minus_male_minutes": (
                    female["mean_minutes_per_person"] - male["mean_minutes_per_person"]
                ),
                "eligible_n_ge_100_each": (
                    int(male["sample_n"] >= 100 and female["sample_n"] >= 100)
                ),
            }
        )
    gap_rows.sort(key=lambda row: row["time_gap_female_minus_male_minutes"], reverse=True)
    write_csv(TABLES / "state_gender_gaps.csv", list(gap_rows[0].keys()), gap_rows)
    return state_rows, gap_rows, group_rows, activity_rows


def household_group_stats(
    households: dict[tuple[str, str], dict[str, str]],
    household_aggregates: dict[tuple[str, str], dict[str, float]],
) -> list[dict[str, str | int | float]]:
    accumulators: dict[tuple[str, str], dict[str, float]] = {}
    fields = (
        "survey_households_n",
        "weighted_survey_households",
        "households_n",
        "weighted_households",
        "scope_households_n",
        "scope_households_w",
        "goods_households_n",
        "goods_households_w",
        "domestic_households_n",
        "domestic_households_w",
        "water_households_n",
        "water_households_w",
        "firewood_households_n",
        "firewood_households_w",
        "positive_homegrown_value_households_n",
        "homegrown_positive_w",
        "person_minutes_w",
        "goods_minutes_w",
        "domestic_minutes_w",
        "water_minutes_w",
        "firewood_minutes_w",
        "person_days_n",
    )

    for key, household in households.items():
        weight = household["Weight"]
        if weight is None:
            continue
        state = household["State_Code"]
        sector = household["Sector"]
        land_code = str(parse_int(household["Land_Possessed"]) or "")
        land_group = LAND_LABELS.get(land_code, "Other/unknown code")
        fuel_code = parse_int(household["Energy_Cooking"])
        fuel_group = (
            "Firewood and chips"
            if fuel_code == 1
            else "Other cooking fuel"
            if fuel_code is not None
            else "Missing cooking fuel"
        )
        stock_value = parse_int(household["Exp_Imputed_B"])
        if stock_value is None:
            stock_group = "Missing monthly imputed home-grown consumption value"
        elif stock_value > 0:
            stock_group = "Positive monthly imputed home-grown consumption value"
        else:
            stock_group = "Zero monthly imputed home-grown consumption value"
        agg = household_aggregates.get(key, {})
        common = agg.get("any_scope", 0) > 0
        goods = agg.get("any_goods", 0) > 0
        domestic = agg.get("any_domestic", 0) > 0
        water = agg.get("any_water", 0) > 0
        firewood = agg.get("any_firewood", 0) > 0
        person_minutes = agg.get("scope_minutes", 0.0)
        person_days = agg.get("diary_people", 0.0)
        household_size = parse_int(household["Household_Size"])
        if household_size is None:
            household_size_group = "Unknown household size"
        elif household_size <= 2:
            household_size_group = "1-2 people"
        elif household_size <= 4:
            household_size_group = "3-4 people"
        elif household_size <= 6:
            household_size_group = "5-6 people"
        else:
            household_size_group = "7+ people"
        group_keys = [
            ("land", land_group),
            ("cooking_fuel", fuel_group),
            ("homegrown_consumption_value", stock_group),
            ("household_size", household_size_group),
            ("sector", SECTOR_NAME.get(sector, "Unknown")),
            ("state", household["State_Name"] or state),
        ]
        for group_key in group_keys:
            bucket = accumulators.setdefault(
                group_key, {name: 0.0 for name in fields}
            )
            bucket["survey_households_n"] += 1
            bucket["weighted_survey_households"] += weight
            if person_days == 0:
                continue
            bucket["households_n"] += 1
            bucket["weighted_households"] += weight
            bucket["scope_households_n"] += common
            bucket["scope_households_w"] += weight * common
            bucket["goods_households_n"] += goods
            bucket["goods_households_w"] += weight * goods
            bucket["domestic_households_n"] += domestic
            bucket["domestic_households_w"] += weight * domestic
            bucket["water_households_n"] += water
            bucket["water_households_w"] += weight * water
            bucket["firewood_households_n"] += firewood
            bucket["firewood_households_w"] += weight * firewood
            positive_homegrown_value = stock_value is not None and stock_value > 0
            bucket["positive_homegrown_value_households_n"] += positive_homegrown_value
            bucket["homegrown_positive_w"] += weight * positive_homegrown_value
            bucket["person_minutes_w"] += weight * person_minutes
            bucket["goods_minutes_w"] += weight * agg.get("goods_minutes", 0.0)
            bucket["domestic_minutes_w"] += weight * agg.get("domestic_minutes", 0.0)
            bucket["water_minutes_w"] += weight * agg.get("water_minutes", 0.0)
            bucket["firewood_minutes_w"] += weight * agg.get("firewood_minutes", 0.0)
            bucket["person_days_n"] += person_days

    rows = []
    for (group_type, group_name), values in accumulators.items():
        denominator = values["weighted_households"]
        row: dict[str, str | int | float] = {
            "group_type": group_type,
            "group": group_name,
            "survey_households_n": int(values["survey_households_n"]),
            "weighted_survey_households": values["weighted_survey_households"],
            "sample_households_n": int(values["households_n"]),
            "observed_diary_households_n": int(values["households_n"]),
            "weighted_households": denominator,
            "households_with_scope_n": int(values["scope_households_n"]),
            "weighted_households_with_scope": values["scope_households_w"],
            "households_with_goods_n": int(values["goods_households_n"]),
            "weighted_households_with_goods": values["goods_households_w"],
            "households_with_domestic_services_n": int(values["domestic_households_n"]),
            "weighted_households_with_domestic_services": values["domestic_households_w"],
            "households_with_water_collection_n": int(values["water_households_n"]),
            "weighted_households_with_water_collection": values["water_households_w"],
            "households_with_firewood_collection_n": int(values["firewood_households_n"]),
            "weighted_households_with_firewood_collection": values["firewood_households_w"],
            "households_with_positive_monthly_imputed_homegrown_consumption_n": int(
                values["positive_homegrown_value_households_n"]
            ),
            "weighted_households_with_positive_monthly_imputed_homegrown_consumption": (
                values["homegrown_positive_w"]
            ),
            "households_with_scope_pct": 100 * values["scope_households_w"] / denominator if denominator else 0,
            "households_with_goods_pct": 100 * values["goods_households_w"] / denominator if denominator else 0,
            "households_with_domestic_services_pct": 100 * values["domestic_households_w"] / denominator if denominator else 0,
            "households_with_water_collection_pct": 100 * values["water_households_w"] / denominator if denominator else 0,
            "households_with_firewood_collection_pct": 100 * values["firewood_households_w"] / denominator if denominator else 0,
            "households_with_positive_monthly_imputed_homegrown_consumption_pct": 100 * values["homegrown_positive_w"] / denominator if denominator else 0,
            "mean_person_minutes_per_household": values["person_minutes_w"] / denominator if denominator else 0,
            "mean_goods_minutes_per_household": values["goods_minutes_w"] / denominator if denominator else 0,
            "mean_domestic_minutes_per_household": values["domestic_minutes_w"] / denominator if denominator else 0,
            "mean_water_minutes_per_household": values["water_minutes_w"] / denominator if denominator else 0,
            "mean_firewood_minutes_per_household": values["firewood_minutes_w"] / denominator if denominator else 0,
            "observed_diary_person_days_n": int(values["person_days_n"]),
        }
        rows.append(row)
    rows.sort(key=lambda row: (str(row["group_type"]), str(row["group"])))
    write_csv(TABLES / "household_dependence.csv", list(rows[0].keys()), rows)
    return rows


def write_state_lookup(state_lookup: dict[str, str]) -> None:
    rows = [
        {"state_code": code, "state_name": name}
        for code, name in sorted(state_lookup.items())
    ]
    write_csv(TABLES / "state_code_lookup.csv", ["state_code", "state_name"], rows)


def svg_text(x: float, y: float, content: str, *, size: int = 14, color: str = "#263547", weight: str = "400", anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, sans-serif" '
        f'font-size="{size}px" fill="{color}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{escape(str(content))}</text>'
    )


def svg_start(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfaf7"/>',
        svg_text(46, 48, title, size=25, color="#18324a", weight="700"),
        svg_text(46, 77, subtitle, size=14, color="#526579"),
    ]


def chart_gender_rural_urban(group_rows: list[dict]) -> None:
    selected = [
        (sector, gender)
        for sector in ("1", "2")
        for gender in ("1", "2")
    ]
    lookup = {
        (str(row["sector"]), str(row["gender"]), str(row["metric"])): row
        for row in group_rows
    }
    width, height = 960, 560
    svg = svg_start(
        width,
        height,
        "Unpaid household work, split by place and gender",
        "Weighted mean minutes/person/day; divisions 2+3. Source: NSO TUS 2024; final weight MLT/100.",
    )
    svg.append('<line x1="120" y1="460" x2="900" y2="460" stroke="#9aa9b6"/>')
    max_value = max(
        float(lookup[(s, g, "household_scope")]["mean_minutes_per_person"])
        for s, g in selected
    ) * 1.15
    colors = ("#1e8a8a", "#e58b45")
    for index, (sector, gender) in enumerate(selected):
        x = 200 + index * 175
        goods = float(lookup[(sector, gender, "goods_own_use")]["mean_minutes_per_person"])
        domestic = float(lookup[(sector, gender, "domestic_services")]["mean_minutes_per_person"])
        total = goods + domestic
        scale = 300 / max_value
        goods_h, domestic_h = goods * scale, domestic * scale
        svg.append(f'<rect x="{x}" y="{460-goods_h:.2f}" width="72" height="{goods_h:.2f}" fill="{colors[0]}"/>')
        svg.append(f'<rect x="{x}" y="{460-goods_h-domestic_h:.2f}" width="72" height="{domestic_h:.2f}" fill="{colors[1]}"/>')
        svg.append(svg_text(x + 36, 484, f"{SECTOR_NAME[sector]} {GENDER_NAME[gender]}", size=13, anchor="middle"))
        svg.append(svg_text(x + 36, 460 - goods_h - domestic_h - 10, f"{total:.0f} min", size=13, weight="700", anchor="middle"))
        if goods_h > 20:
            svg.append(svg_text(x + 36, 460 - goods_h / 2 + 5, f"{goods:.0f}", size=11, color="#ffffff", weight="700", anchor="middle"))
        if domestic_h > 22:
            svg.append(svg_text(x + 36, 460 - goods_h - domestic_h / 2 + 5, f"{domestic:.0f}", size=11, color="#ffffff", weight="700", anchor="middle"))
    svg.append('<rect x="250" y="520" width="14" height="14" fill="#1e8a8a"/>')
    svg.append(svg_text(271, 532, "Own-use goods production", size=12))
    svg.append('<rect x="490" y="520" width="14" height="14" fill="#e58b45"/>')
    svg.append(svg_text(511, 532, "Unpaid domestic services", size=12))
    svg.append(svg_text(46, 553, "Minutes are equal-allocation estimates, so simultaneous activities do not add overlapping time.", size=11, color="#637487"))
    svg.append("</svg>")
    write_text(CHARTS / "gender_rural_urban.svg", "\n".join(svg))


def chart_activity_times(activity_rows: list[dict]) -> None:
    selected = [
        "crops_211", "animals_212", "wild_products_215",
        "firewood_241", "water_242", "other_goods", "domestic_services",
    ]
    lookup = {str(row["metric"]): row for row in activity_rows}
    width, height = 1000, 600
    svg = svg_start(
        width,
        height,
        "What household production takes, minute by minute",
        "Weighted average minutes/day among participants; NSO TUS 2024, final weight MLT/100.",
    )
    baseline = 485
    max_minutes = max(float(lookup[key]["mean_minutes_per_participant"]) for key in selected)
    scale = 315 / max_minutes if max_minutes else 0
    colors = ["#1e8a8a", "#1e8a8a", "#1e8a8a", "#e58b45", "#3979a8", "#8497a7", "#d56b50"]
    for i, key in enumerate(selected):
        x = 85 + i * 128
        value = float(lookup[key]["mean_minutes_per_participant"])
        bar_h = value * scale
        svg.append(f'<rect x="{x}" y="{baseline-bar_h:.2f}" width="70" height="{bar_h:.2f}" fill="{colors[i]}"/>')
        svg.append(svg_text(x + 35, baseline - bar_h - 9, f"{value:.0f}", size=13, weight="700", anchor="middle"))
        label = {
            "crops_211": "Crops /",
            "animals_212": "Animals",
            "wild_products_215": "Wild",
            "firewood_241": "Firewood",
            "water_242": "Water",
            "other_goods": "Other goods",
            "domestic_services": "Domestic",
        }[key]
        code = {
            "crops_211": "211",
            "animals_212": "212",
            "wild_products_215": "215",
            "firewood_241": "241",
            "water_242": "242",
            "other_goods": "2xx",
            "domestic_services": "3xx",
        }[key]
        svg.append(svg_text(x + 35, 510, label, size=12, anchor="middle"))
        svg.append(svg_text(x + 35, 529, code, size=11, color="#637487", anchor="middle"))
    svg.append('<line x1="60" y1="485" x2="970" y2="485" stroke="#9aa9b6"/>')
    svg.append(svg_text(46, 575, "Non-participants contribute zero to the population mean; these bars condition on participation.", size=11, color="#637487"))
    svg.append("</svg>")
    write_text(CHARTS / "activity_times.svg", "\n".join(svg))


def chart_states(state_rows: list[dict]) -> None:
    eligible = [row for row in state_rows if row["sample_n"] >= 100]
    chosen = sorted(eligible, key=lambda row: float(row["participation_rate_pct"]), reverse=True)[:12]
    chosen.reverse()
    width, height = 1120, 700
    svg = svg_start(
        width,
        height,
        "Where unpaid household production is most common",
        "Top 12 by weighted participation in ICATUS divisions 2+3; NSO TUS 2024, MLT/100.",
    )
    left, top, row_h = 270, 120, 40
    max_pct = max(float(row["participation_rate_pct"]) for row in chosen) if chosen else 1
    for i, row in enumerate(chosen):
        y = top + i * row_h
        pct = float(row["participation_rate_pct"])
        minutes = float(row["mean_minutes_per_person"]) / 60
        bar_w = 500 * pct / max_pct
        svg.append(svg_text(left - 14, y + 18, row["state_name"], size=13, anchor="end"))
        svg.append(f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="22" rx="3" fill="#1e8a8a"/>')
        svg.append(svg_text(left + bar_w + 8, y + 16, f"{pct:.1f}%  |  {minutes:.2f} h/day  |  n={row['sample_n']:,}", size=12))
    svg.append(svg_text(46, height - 24, "Ranks use participation; all displayed states/UTs have at least 100 valid diaries.", size=11, color="#637487"))
    svg.append("</svg>")
    write_text(CHARTS / "state_comparison.svg", "\n".join(svg))


def chart_gender_gaps(gap_rows: list[dict]) -> None:
    eligible = [row for row in gap_rows if row["eligible_n_ge_100_each"]]
    selected = sorted(eligible, key=lambda row: row["time_gap_female_minus_male_minutes"], reverse=True)[:12]
    selected.reverse()
    width, height = 1120, 700
    svg = svg_start(
        width,
        height,
        "The largest female-minus-male gaps",
        "State/UT gap in weighted mean minutes/day; women minus men. NSO TUS 2024, MLT/100.",
    )
    left, top, row_h = 270, 120, 40
    max_gap = max((float(row["time_gap_female_minus_male_minutes"]) for row in selected), default=1)
    scale = 520 / max_gap if max_gap > 0 else 0
    for i, row in enumerate(selected):
        y = top + i * row_h
        gap = float(row["time_gap_female_minus_male_minutes"])
        bar_w = max(0, gap * scale)
        svg.append(svg_text(left - 14, y + 18, row["state_name"], size=13, anchor="end"))
        svg.append(f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="22" rx="3" fill="#d56b50"/>')
        svg.append(svg_text(left + bar_w + 8, y + 16, f"+{gap:.0f} min ({gap/60:.2f} h)  |  n={row['female_sample_n']:,}/{row['male_sample_n']:,}", size=12))
    svg.append(svg_text(46, height - 24, "Only states/UTs with at least 100 valid diaries per gender are ranked.", size=11, color="#637487"))
    svg.append("</svg>")
    write_text(CHARTS / "largest_gender_gaps.svg", "\n".join(svg))


def main() -> None:
    global OVERWRITE
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace outputs previously created by this script.",
    )
    OVERWRITE = parser.parse_args().overwrite
    PROCESSED.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    state_lookup = read_state_lookup(LAYOUT_FILE)
    write_state_lookup(state_lookup)
    households, household_quality = read_households(state_lookup)
    if household_quality["unique_household_keys"] != EXPECTED_HOUSEHOLD_ROWS:
        raise ValueError("Household identifiers do not match the official record count.")
    write_processed_households(households)
    household_aggregates, person_quality, domains = write_person_summaries(
        households, state_lookup
    )
    person_quality.update(
        {
            "household_file_rows": household_quality["rows"],
            "unique_household_keys": household_quality["unique_household_keys"],
            "household_fsu_count": household_quality["fsu_count"],
            "household_weight_sum": household_quality["weight_sum"],
            "duplicate_household_keys": household_quality.get("duplicate_household_keys", 0),
            "household_fsu_weight_variations": household_quality.get("fsu_weight_variations", 0),
            "person_household_unmatched": person_quality.get("person_household_unmatched", 0),
        }
    )
    person_quality["household_file_row_count_matches_ddi"] = (
        household_quality["rows"] == EXPECTED_HOUSEHOLD_ROWS
    )
    person_quality["household_weight_sum"] = household_quality["weight_sum"]
    person_quality["person_file_size_bytes"] = PERSON_FILE.stat().st_size
    person_quality["household_file_size_bytes"] = HOUSEHOLD_FILE.stat().st_size
    person_quality["included_weighted_person_days"] = person_quality.get("person_weight_sum", 0)
    for diagnostic in (
        "invalid_activity_time",
        "invalid_activity_duration",
        "invalid_or_missing_activity_code",
        "goods_status_not_04",
        "domestic_status_not_03",
        "activity_member_not_diary_person",
        "person_household_weight_mismatch",
        "person_household_geography_mismatch",
        "diaries_slot_duration_not_1440",
        "diaries_union_duration_not_1440",
        "nonidentical_overlapping_intervals",
        "major_activity_flag_slot_errors",
        "multiple_activity_flag_slot_errors",
        "inconsistent_person_fields",
        "persons_excluded_missing_core_values",
        "persons_invalid_response_code",
    ):
        person_quality[diagnostic] = person_quality.get(diagnostic, 0)
    domain_rows = [
        {"variable": variable, "value": value or "<blank>", "frequency": frequency}
        for variable, counts in sorted(domains.items())
        for value, frequency in sorted(counts.items())
    ]
    write_csv(
        TABLES / "activity_code_domain.csv",
        ["variable", "value", "frequency"],
        domain_rows,
    )

    groups = aggregate_person_metrics()
    state_rows, gap_rows, gender_rows, activity_rows = write_metric_tables(
        groups, state_lookup
    )
    major_groups = aggregate_person_metrics("major")
    major_rows = [
        row
        for row in metric_to_rows(major_groups, state_lookup)
        if row["group_type"] in {"all", "sector_gender"}
        and row["metric"] in {"goods_own_use", "domestic_services", "household_scope"}
    ]
    major_fields = list(dict.fromkeys(key for row in major_rows for key in row))
    write_csv(TABLES / "major_only_sensitivity.csv", major_fields, major_rows)
    household_rows = household_group_stats(households, household_aggregates)
    person_quality["household_summary_groups"] = len(household_rows)
    person_quality["households_with_valid_diaries"] = len(household_aggregates)
    person_quality["households_without_valid_diaries"] = (
        len(households) - len(household_aggregates)
    )
    person_quality["weight_of_households_with_valid_diaries"] = sum(
        households[key]["Weight"] or 0
        for key in household_aggregates
    )
    person_quality["state_count"] = len(state_lookup)
    person_quality["person_file_row_count_matches_ddi"] = (
        person_quality.get("person_file_rows") == EXPECTED_PERSON_ROWS
    )
    quality_text = json.dumps(
        {
                "expected": {
                    "person_records": EXPECTED_PERSON_ROWS,
                    "household_records": EXPECTED_HOUSEHOLD_ROWS,
                    "target_age_minimum": TARGET_MIN_AGE,
                    "final_weight": "MULT / 100",
                    "time_method": "equal allocation across distinct activities in the same recorded interval",
                    "diary_denominator": (
                        "valid completed person-day records age 6+ with at least one valid activity interval"
                    ),
                },
                "person": person_quality,
                "household": household_quality,
                "observed_domains": {
                    key: dict(value) for key, value in domains.items()
                },
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    write_text(TABLES / "quality_checks.json", quality_text)

    chart_gender_rural_urban(gender_rows)
    chart_activity_times(activity_rows)
    chart_states(state_rows)
    chart_gender_gaps(gap_rows)
    print(
        f"Analyzed {person_quality['person_file_rows']:,} person records; "
        f"{person_quality['valid_person_days']:,} valid age-6+ diaries; "
        f"{household_quality['rows']:,} household records."
    )


def metric_to_rows(
    groups: dict[tuple[str, ...], dict[str, MetricAccumulator]],
    state_lookup: dict[str, str],
) -> list[dict]:
    return [
        metric_output(key, metric, accumulator, state_lookup)
        for key, metrics in groups.items()
        for metric, accumulator in metrics.items()
    ]


if __name__ == "__main__":
    main()
