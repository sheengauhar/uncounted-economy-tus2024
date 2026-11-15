"""Independently re-aggregate processed TUS person-days and check output tables."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import gzip
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERSON_DAYS = ROOT / "data" / "processed" / "person_day_summary.csv.gz"
ESTIMATES = ROOT / "outputs" / "tables" / "person_group_estimates.csv"
OUTPUT = ROOT / "outputs" / "tables" / "independent_validation.json"
METRICS = (
    "goods_own_use",
    "domestic_services",
    "household_scope",
    "crops_211",
    "animals_212",
    "wild_products_215",
    "firewood_241",
    "water_242",
    "other_goods",
)
COMPARE_FIELDS = (
    "sample_n",
    "weighted_population",
    "participants_n",
    "weighted_participants",
    "weighted_minutes_total",
    "participation_rate_pct",
    "mean_minutes_per_person",
    "mean_minutes_per_participant",
)
TOLERANCE = 1e-6


def new_accumulator() -> dict[str, float]:
    return {
        "sample_n": 0,
        "weighted_population": 0.0,
        "participants_n": 0,
        "weighted_participants": 0.0,
        "weighted_minutes_total": 0.0,
        "weighted_participant_minutes": 0.0,
    }


def add(accumulator: dict[str, float], weight: float, minutes: float, participates: bool) -> None:
    accumulator["sample_n"] += 1
    accumulator["weighted_population"] += weight
    accumulator["weighted_minutes_total"] += weight * minutes
    if participates:
        accumulator["participants_n"] += 1
        accumulator["weighted_participants"] += weight
        accumulator["weighted_participant_minutes"] += weight * minutes


def finish(accumulator: dict[str, float]) -> dict[str, float]:
    population_weight = accumulator["weighted_population"]
    participant_weight = accumulator["weighted_participants"]
    return {
        "sample_n": int(accumulator["sample_n"]),
        "weighted_population": population_weight,
        "participants_n": int(accumulator["participants_n"]),
        "weighted_participants": participant_weight,
        "weighted_minutes_total": accumulator["weighted_minutes_total"],
        "participation_rate_pct": (
            100 * participant_weight / population_weight if population_weight else 0.0
        ),
        "mean_minutes_per_person": (
            accumulator["weighted_minutes_total"] / population_weight
            if population_weight
            else 0.0
        ),
        "mean_minutes_per_participant": (
            accumulator["weighted_participant_minutes"] / participant_weight
            if participant_weight
            else 0.0
        ),
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def main() -> None:
    groups: dict[tuple[str, str], dict[str, dict[str, float]]] = defaultdict(dict)
    rural_women_sensitivity: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    response_code_counts: Counter[str] = Counter()
    rows = 0
    max_scope_minutes = 0.0
    max_category_reconciliation_error = 0.0
    with gzip.open(PERSON_DAYS, "rt", encoding="utf-8", newline="") as source:
        for person in csv.DictReader(source):
            rows += 1
            weight = float(person["Weight"])
            response_code_counts[person["Response_Code"]] += 1
            if weight <= 0:
                raise AssertionError(f"Non-positive person weight at record {rows}.")
            goods = float(person["Equal_Minutes_goods_own_use"])
            domestic = float(person["Equal_Minutes_domestic_services"])
            scope = float(person["Equal_Minutes_household_scope"])
            max_scope_minutes = max(max_scope_minutes, scope)
            max_category_reconciliation_error = max(
                max_category_reconciliation_error, abs(scope - goods - domestic)
            )
            if scope < 0 or scope > 1440:
                raise AssertionError(f"Invalid household-scope duration at record {rows}.")

            keys = [
                ("all", "India"),
                ("sector_gender", f"{person['Sector']}|{person['Gender']}"),
            ]
            for group_type, group_code in keys:
                bucket = groups[(group_type, group_code)]
                for metric in METRICS:
                    accumulator = bucket.setdefault(metric, new_accumulator())
                    minutes = float(person[f"Equal_Minutes_{metric}"])
                    participates = person[f"Participates_{metric}"] == "1"
                    if minutes < 0 or minutes > 1440:
                        raise AssertionError(
                            f"Invalid {metric} duration at person-day record {rows}."
                        )
                    add(accumulator, weight, minutes, participates)
            if person["Sector"] == "1" and person["Gender"] == "2":
                for response_group in ("all", person["Response_Code"]):
                    if response_group != "all" and response_group != "1":
                        continue
                    bucket = rural_women_sensitivity[response_group]
                    for metric in ("household_scope", "goods_own_use", "domestic_services"):
                        accumulator = bucket.setdefault(metric, new_accumulator())
                        add(
                            accumulator,
                            weight,
                            float(person[f"Equal_Minutes_{metric}"]),
                            person[f"Participates_{metric}"] == "1",
                        )

    recomputed = {
        (group_type, group_code, metric): finish(accumulator)
        for (group_type, group_code), metrics in groups.items()
        for metric, accumulator in metrics.items()
    }
    official = {
        (row["group_type"], row["group_code"], row["metric"]): row
        for row in read_csv(ESTIMATES)
        if row["group_type"] in {"all", "sector_gender"}
    }
    failures: list[str] = []
    compared = 0
    for key, calculated in recomputed.items():
        published = official.get(key)
        if published is None:
            failures.append(f"Missing estimate table row: {key}")
            continue
        compared += 1
        for field in COMPARE_FIELDS:
            expected = float(published[field])
            actual = float(calculated[field])
            if abs(expected - actual) > TOLERANCE:
                failures.append(
                    f"{key} {field}: output={expected}, independent={actual}"
                )
        rate = float(calculated["participation_rate_pct"])
        if not 0 <= rate <= 100:
            failures.append(f"Out-of-range participation rate: {key}={rate}")
    if len(official) != compared:
        failures.append(
            f"Compared {compared} rows but found {len(official)} relevant table rows."
        )
    if max_category_reconciliation_error > TOLERANCE:
        failures.append(
            "Own-use goods plus domestic services do not reconcile to combined time: "
            f"max error {max_category_reconciliation_error} minutes."
        )
    if failures:
        raise AssertionError("\n".join(failures[:30]))

    response_sensitivity = {}
    for metric in ("household_scope", "goods_own_use", "domestic_services"):
        all_people = finish(rural_women_sensitivity["all"][metric])
        cooperative = finish(rural_women_sensitivity["1"][metric])
        response_sensitivity[metric] = {
            "all_valid_diaries": all_people,
            "response_code_1_only": cooperative,
            "mean_minutes_delta_code1_minus_all": (
                cooperative["mean_minutes_per_person"]
                - all_people["mean_minutes_per_person"]
            ),
            "participation_rate_delta_pp_code1_minus_all": (
                cooperative["participation_rate_pct"]
                - all_people["participation_rate_pct"]
            ),
        }

    result = {
        "status": "passed",
        "person_day_records_recomputed": rows,
        "estimate_rows_compared": compared,
        "metric_rows_compared": len(METRICS),
        "groups_compared": len(recomputed) // len(METRICS),
        "fields_compared_per_row": list(COMPARE_FIELDS),
        "tolerance": TOLERANCE,
        "max_scope_minutes": max_scope_minutes,
        "max_goods_plus_domestic_reconciliation_error_minutes": (
            max_category_reconciliation_error
        ),
        "response_code_counts": dict(response_code_counts),
        "rural_women_response_sensitivity": response_sensitivity,
        "checks": [
            "positive final person weights",
            "durations are within 0-1440 minutes",
            "participation rates are within 0-100 percent",
            "weighted estimates match the independent aggregation",
            "goods plus domestic services reconcile to combined time",
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
