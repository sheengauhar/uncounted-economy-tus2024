# The Uncounted Economy
## Water, firewood, cattle: work that GDP does not see

### Executive result

The strongest age-specific result is not simply that women do more unpaid work: **rural women aged 25-44 averaged 370.3 minutes (6 h 10 m) per person per day in own-use goods production and unpaid domestic services**. The weighted participation rate was 98.74%. Rural men aged 25-44 averaged 53.6 minutes per person per day, a descriptive gap of 316.7 minutes (5 h 17 m).

Two complementary poster findings make the burden concrete:

- Households using firewood and chips as their primary cooking fuel were about **14.8 times as likely** to have a valid diary member collect firewood as households using other recorded cooking fuels: 18.64% versus 1.26%.
- In Arunachal Pradesh, women averaged 396.6 minutes/day in the selected activities, versus 84.5 for men: a female-minus-male gap of 312.2 minutes (5 h 12 m). This is a weighted descriptive contrast, not a significance test.

All findings below are descriptive estimates. No standard errors, confidence intervals, or tests of statistical significance were calculated.

## 1. Data and extraction

### Source files and structure

The official source is the [MoSPI/NSSO TUS 2024 catalogue](https://microdata.gov.in/NADA/index.php/catalog/236). Downloaded microdata and official documentation are not included in the public project copy; obtain them from the catalogue and follow its access and reuse terms.

| Dataset | Raw export | Official DDI cases | Unit and use |
|---|---|---:|---|
| `tus106PER` | Not included; generate locally from the official Nesstar archive | 10,211,478 | Repeated person/activity rows. Diary attributes are repeated across a person's activity rows; blank activity serials also occur for roster/non-diary rows. |
| `tus106HH` | Not included; generate locally from the official Nesstar archive | 139,489 | Household characteristics; one row per unique `(FSU_Serial_No, Sample_HH_No)` key. |

The DDI reports 41 person-file variables and 37 household-file variables. Raw exports are CSV text, so fields are initially strings; the analysis parses weights, ages, intervals, and selected household characteristics as needed. The downloaded ZIP was preserved. The unsigned Nesstar installer was not run and no software was installed.

The `.Nesstar` records were read with the pinned, locally vendored parser and a reproducible DDI-mapped exporter in [`analysis/extract_nesstar.py`](../analysis/extract_nesstar.py). The export was checked against the official DDI for dataset names, record counts, and variable names/order. This is a reverse-engineered container route rather than an official Nesstar export, so the structural cross-checks are important.

The second project ZIP, `Date of Survey_TUS2024(which has the household level file structure).zip`, contains `TUS24DOS_DDMMYYYY.TXT` and `TUS24DOS_YYYYMMDD.TXT`. Each has 139,489 fixed-width records of 26 bytes. These supplemental files were kept out of the analysis because the person/activity data already contain `Day_of_Week` and `Type_of_Day`, and no date-specific comparison was needed for the chosen questions. Raw files are not included in the public project copy.

### Identifiers and analysis variables

| File | Variables used | Meaning / processing |
|---|---|---|
| Person/activity | `FSU_Serial_No`, `Sample_HH_No`, `Person_Serial_No` | Household and person keys. Person diary rows are grouped by all three values; observed groups are contiguous and no person group reappears later in the file. |
| Person/activity | `Activity_Code_3Digit` | Activity code. Aggregated to ICATUS divisions and selected 3-digit codes. |
| Person/activity | `Time_From`, `Time_To` | Activity interval, parsed from `HH:MM`. Seven intervals had invalid/non-positive durations; they were excluded. |
| Person/activity | `Multiple_Activity_Flag`, `Simultaneous_Activity_Flag`, `Major_Activity_Flag` | Checked against the observed activity rows and shared time intervals. Equal allocation is primary; major-only is retained as a sensitivity estimate. |
| Person/activity | `Unpaid_Paid_Status` | Checked against the goods/services domains: zero observed code-2/status-04 or code-3/status-03 mismatches. |
| Person/activity | `Gender`, `Age`, `Sector`, `NSS_Region` | Gender, age, rural/urban, and state. First two digits of `NSS_Region` were mapped using the official layout workbook; all 36 state/UT codes mapped. |
| Person/activity and household | `MULT` | Official final weight is `MULT / 100`; the multiplier is applied once per person-day or once per household, not once per activity row. |
| Household | `Household_Size`, `Land_Possessed`, `Energy_Cooking` | Household size, land possessed on survey date, and primary cooking fuel. |
| Household | `Exp_Imputed_B` | Official label: imputed usual monthly consumption value from home-grown stock. Used only as a positive/zero/missing household characteristic; it is not a physical stock measure. |

The official NMDS 2.0 metadata says activities were coded to ICATUS 2016, gives the indicator formulas, and sets the diary population at age 6+. The DDI, survey README, data layout, and field-code workbook were used to validate fields, codes, weights, and identifiers. These official documents are downloadable from the [MoSPI/NSSO catalogue](https://microdata.gov.in/NADA/index.php/catalog/236) and are not redistributed here.

### Activity scope and definitions

The analysis keeps two concepts separate:

- **ICATUS division 2:** production of goods for own final use.
- **ICATUS division 3:** unpaid domestic services for the household/family.
- **Combined household-work measure:** divisions 2 and 3, with shared-slot time allocated across activities.
- **Excluded:** division 4 unpaid caregiving and all other themes outside the project scope.

Relevant own-use goods codes, verified against the ICATUS 2016 coding used by the survey and against the observed activity-code domain:

| Code | Activity |
|---:|---|
| 211 | Growing crops and kitchen gardening for own final use |
| 212 | Farming animals and producing animal products for own final use |
| 215 | Gathering wild products for own final use |
| 241 | Gathering firewood and other natural products used as fuel for own final use |
| 242 | Fetching water for own final use |

All five codes occurred in the records. The full observed code frequencies are in [`activity_code_domain.csv`](../outputs/tables/activity_code_domain.csv).

## 2. Processing, weighting, and validation

### Person-day construction

1. Read all 10,211,478 person-file rows and group activity rows by FSU, sample household, and person serial.
2. Verify person-level fields remain constant within each person's rows. No inconsistent person fields or non-contiguous person groups were found.
3. Keep age-6+ diary people with at least one valid activity interval, valid gender/sector/state, and a valid final multiplier.
4. For simultaneous activities sharing a time interval, divide that interval equally among the distinct activities. This follows the official README's allowed all-activities treatment. The alternative major-activity-only treatment is saved in [`major_only_sensitivity.csv`](../outputs/tables/major_only_sensitivity.csv).
5. Add each person once to denominators. A person participating in an activity contributes the person's final weight to that activity's weighted numerator.

The survey README defines:

- Participation = weighted participants / weighted persons in the target population.
- Mean time per participant = weighted activity minutes / weighted participants.
- Mean time per person = weighted activity minutes / all weighted persons; nonparticipants contribute zero.

Consequently, average time among participants must not be confused with the all-person mean. Activities are not added as if overlapping simultaneous time were distinct clock time.

### Household analysis

Household outcomes use the household multiplier, separately from person estimates. A household counts as participating if at least one valid diary member in that household participated in the activity. Household outcome denominators include only households with at least one valid diary member; six of 139,489 household records had no valid diary and are excluded from household outcome estimates. The household total-minutes measure sums the reported minutes of valid diary members and divides by observed household weight; it is not a per-person estimate.

### Validation results

| Check | Result |
|---|---:|
| Person activity rows vs DDI | 10,211,478 vs 10,211,478 |
| Household rows vs DDI | 139,489 vs 139,489 |
| Unique household keys / duplicate keys | 139,489 / 0 |
| Unique FSUs in records | 9,969 |
| Person groups including roster-only rows | 533,719 |
| Age-6+ persons listed in the data | 454,192 |
| Valid age-6+ diaries analyzed | 450,452 |
| Age-6+ people with no diary rows | 3,735 |
| People with no valid activity interval | 5 |
| Under-6 roster-only person groups | 79,527 |
| Invalid activity intervals | 7 |
| Diaries whose recorded intervals failed the 1,440-minute total/union check | 0 |
| Non-identical overlapping intervals | 0 |
| Multiple-activity flag inconsistencies / invalid major-activity slots | 0 / 0 |
| Household joins missing / person-household weight mismatches | 0 / 0 |
| Gender, sector, or state exclusions for invalid core values | 0 |

Among the household characteristics used, `Household_Size`, `Land_Possessed`, and `Energy_Cooking` each have two blank records; `Exp_Imputed_B` is blank for 7,858 of 139,489 household rows. The missing home-grown consumption value is kept as an explicit group, not imputed by this analysis. The 2 missing values in the other fields are retained as missing/unknown groups; they are not assigned a substantive category.

The person file contains 10,128,216 activity rows according to the DDI; seven had invalid duration, leaving 10,128,209 valid activity rows. The remaining person-file rows without an activity serial include under-6 roster records and age-6+ people with no diary.

There are two metadata differences to retain rather than silently reconcile: the exported household file and DDI contain 139,489 rows, while the NMDS summary says 139,487 households surveyed; the DDI also reports two invalid `Household_Size` values. The exported data has 139,489 unique household keys. The NMDS sample summary lists 454,192 age-6+ people, matching the number of age-6+ person groups before excluding 3,740 records without a usable diary.

The independent aggregation in [`analysis/validate_results.py`](../analysis/validate_results.py) re-read all 450,452 processed person-days and matched 63 national and sector-by-gender table rows across all nine metrics to within `1e-6`. It also verified time bounds, rates, positive weights, and exact reconciliation of goods + domestic-service minutes to combined minutes.

## 3. Ranked candidate findings

Weighted numerator and denominator values below are person equivalents for person estimates and household equivalents for household estimates. `n` and participant counts are unweighted sample counts. The reported intervals are descriptive; no design-based uncertainty estimates are available.

### 1. The six-hour day is concentrated among rural women aged 25-44

- **Finding:** 98.74% participate in divisions 2+3; weighted mean is 370.33 minutes (6 h 10 m) per person per day. Rural men of the same age average 53.61 minutes (44.28% participation).
- **Numerator / denominator:** women 123,645,202.57 / 125,223,039.33 weighted persons; men 52,692,675 / 118,999,805.
- **Population and sample:** rural age 25-44; women `n=53,144`, `52,441` unweighted participants; men `n=50,490`, `22,996` participants.
- **Breakdown:** rural women average 337.06 minutes/day in division 3 and 33.27 in division 2.
- **Variables/codes:** `Age`, `Gender`, `Sector`, `Activity_Code_3Digit`; divisions 2+3, with division-2 codes 211/212/215/241/242 shown separately.
- **Method/limitations:** `MULT/100`; equal allocation for shared intervals. A very strong descriptive age/gender contrast, not a causal or design-significance claim.
- **Chart:** age-by-gender dumbbell or paired bars, with division-2 and division-3 stacks.

### 2. Firewood cooking and firewood collection cluster in the same households

- **Finding:** 18.64% of households using firewood/chips as their primary cooking fuel had a valid diary member collect firewood, versus 1.26% of households using other recorded cooking fuels (about 14.8 times as high).
- **Numerator / denominator:** firewood fuel 16,203,231.82 / 86,948,569.99 weighted households; other fuels 2,778,620.88 / 220,387,270.24.
- **Population and sample:** observed diary households; firewood fuel `n=36,690` (`6,940` with a diary collector), other fuels `n=102,793` (`1,366` with a collector).
- **Variables/codes:** `Energy_Cooking` code 1 and person activity code 241.
- **Method/limitations:** household `MULT/100`; household outcome is any valid diary member. Association only; cooking fuel does not establish that the same household member collected it or why.
- **Chart:** two-dot rate comparison or a 14.8x callout.

### 3. Arunachal Pradesh has the largest measured state gender gap in the combined scope

- **Finding:** women average 396.62 minutes/day (6 h 37 m) versus 84.46 minutes (1 h 24 m) for men; gap 312.16 minutes (5 h 12 m). Participation is 90.28% versus 63.93%.
- **Numerator / denominator:** women 407,552.32 / 451,421.52 weighted persons; men 311,883.22 / 487,851.50.
- **Population and sample:** Arunachal Pradesh; women `n=1,898` (`1,699` participants), men `n=2,062` (`1,268` participants).
- **Variables/codes:** `NSS_Region`, `Gender`, divisions 2+3.
- **Method/limitations:** person `MULT/100`, equal allocation. State-level descriptive ranking; no standard errors, and comparisons are not adjusted for age or household characteristics.
- **Chart:** ranked female-minus-male state gap bar chart.

### 4. The overall rural gender contrast is mostly domestic services, not own-use goods

- **Finding:** rural women average 266.23 minutes/day (4 h 26 m) across divisions 2+3 and have 82.70% participation; rural men average 47.94 minutes (48 m) and have 37.24% participation. Urban women are at 236.14 minutes and 81.14%; urban men at 27.70 minutes and 30.16%. Rural women's 237.69 domestic-service minutes account for 89.3% of their combined mean; own-use goods contribute 28.54 minutes.
- **Numerator / denominator:** rural women 281,378,789.75 / 340,226,113.49 weighted persons; rural men 125,806,000 / 337,864,083.98; urban women 120,000,553.05 / 147,887,389.75; urban men 47,234,042.54 / 156,618,837.89.
- **Population and sample:** rural women `n=142,080` (`117,874` participants); rural men `n=140,900` (`53,849`); urban women `n=81,801` (`66,582`); urban men `n=85,622` (`26,954`). Urban means are 236.14 minutes/day for women and 27.70 for men.
- **Variables/codes:** `Sector`, `Gender`, activity codes in divisions 2 and 3.
- **Method/limitations:** person `MULT/100`; equal allocation; 6+ valid diary respondents. Division 4 care is not included.
- **Chart:** four stacked bars (goods vs domestic services) by rural/urban and gender.

### 5. Rural women participate more in own-use goods production, but participating men spend longer

- **Finding:** rural women's goods-production participation is 25.81% versus 17.43% for men; however, among participants, men average 141.62 minutes/day versus 110.61 for women. Per-person means are closer: 28.54 versus 24.69 minutes/day.
- **Numerator / denominator:** women 87,800,551.20 / 340,226,113.49 weighted persons; men 58,903,075.21 / 337,864,083.98.
- **Population and sample:** rural women `n=142,080` (`37,917` participants); rural men `n=140,900` (`25,501` participants).
- **Variables/codes:** `Sector`, `Gender`, division 2; 211/212/215/241/242 are highlighted sub-activities.
- **Method/limitations:** person `MULT/100`, equal allocation. Participation and duration among participants are different estimands; this is not a productivity comparison.
- **Chart:** paired participation-rate and participant-time panels.

### 6. Own-use goods participation rises with land possessed

- **Finding:** 19.74% of households in the `<0.01 ha` group had a diary member perform division-2 goods production, versus 52.46% in the `1.0-2.5 ha` group.
- **Numerator / denominator:** `<0.01 ha`: 15,797,320.37 / 80,013,275.11 weighted observed households; `1.0-2.5 ha`: 18,474,313.43 / 35,216,742 weighted observed households.
- **Population and sample:** observed diary households; `n=36,199` and `n=15,619`.
- **Variables/codes:** household `Land_Possessed`, and person division-2 activity codes.
- **Method/limitations:** household `MULT/100`; the lowest bracket means less than 0.01 ha, not necessarily exactly landless. Association only; the household diary outcome is based on observed members.
- **Chart:** land-bracket bars with sample counts.

### 7. Larger households have more household hours and a higher chance of any goods production

- **Finding:** household goods-production participation is 24.17% for 1-2-person households and 49.69% for 7+ person households. Household-summed division-2+3 diary time is 4.65 versus 13.36 hours/day.
- **Numerator / denominator:** 1-2 people: 19,392,212.88 / 80,232,708.80 weighted observed households; 7+ people: 10,288,458.38 / 20,706,461.62.
- **Population and sample:** observed diary households; `n=34,826` and `n=9,670`.
- **Variables/codes:** `Household_Size`, divisions 2+3.
- **Method/limitations:** household `MULT/100`; household-summed time naturally includes more potential diary members in larger households and must not be interpreted as per-person burden or causation.
- **Chart:** participation bars plus a separate household-hours line.

### 8. Water collection is both rural and strongly gendered

- **Finding:** rural women have 11.08% water-collection participation versus 3.57% for rural men; urban rates are 6.60% and 1.87%. Participating rural women average 41.72 minutes/day.
- **Numerator / denominator:** rural women 37,703,716.63 / 340,226,113.49 weighted persons; rural men 12,074,432.31 / 337,864,083.98; urban women 9,766,980.77 / 147,887,389.75; urban men 2,933,273.62 / 156,618,837.89.
- **Population and sample:** rural women `n=142,080` (`15,632` participants); rural men `n=140,900` (`5,160`); urban women `n=81,801` (`4,921`); urban men `n=85,622` (`1,590`).
- **Variables/codes:** `Sector`, `Gender`, activity code 242.
- **Method/limitations:** person `MULT/100`, equal allocation. National averages mask state and local access differences.
- **Chart:** four-group participation bars with participant-time labels.

### 9. State rankings change depending on whether prevalence or daily minutes are ranked

- **Finding:** Nagaland has the highest combined-scope participation rate at 78.79%; Arunachal Pradesh has the highest all-person mean at 234.60 minutes/day. Gujarat is lowest on participation among the states/UTs in this export at 49.23%.
- **Numerator / denominator:** Nagaland 845,736.62 / 1,073,473.01 weighted persons; Gujarat 25,602,167.95 / 52,004,758.40. Arunachal rate numerator/denominator: 719,736.23 / 939,573.71.
- **Population and sample:** Nagaland `n=3,420`; Gujarat `n=21,757`; Arunachal `n=3,962`.
- **Variables/codes:** `NSS_Region`, divisions 2+3.
- **Method/limitations:** person `MULT/100`; all 36 state/UTs are in [`state_comparison.csv`](../outputs/tables/state_comparison.csv). Rankings are descriptive and not adjusted for population composition.
- **Chart:** ranked state participation bars annotated with mean minutes/person/day.

### 10. Cattle and animal products are a more common own-use activity than water or firewood

- **Finding:** animal farming (212) participation is 7.13%, with participants averaging 105.62 minutes/day. Water collection (242) is 6.36% and 40.26 minutes among participants; firewood (241) is 2.09% and 74.16 minutes among participants.
- **Numerator / denominator:** animal farming 70,108,721 / 982,696,851.92 weighted persons; water 62,488,564 / 982,696,851.92; firewood 20,536,330 / 982,696,851.92.
- **Population and sample:** all 450,452 valid age-6+ diaries; unweighted participants are 29,390 for animals, 27,307 for water, and 9,020 for firewood.
- **Variables/codes:** `Activity_Code_3Digit`: 212, 242, 241.
- **Method/limitations:** person `MULT/100`, equal allocation; time is participant-conditioned for the duration figures. Participation across codes is non-exclusive and must not be summed.
- **Chart:** activity bars for participation and a separate participant-time scale.

## 4. Three strongest discoveries and recommended poster

### Strongest discovery A: the six-hour day

For rural women aged 25-44:

- Weighted participation: `123,645,202.57 / 125,223,039.33 = 98.74%`.
- Unweighted diary count: `53,144`; unweighted participants: `52,441`.
- Weighted mean: `370.33 minutes/person/day = 6 h 10 m`.
- Breakdown: `337.06 minutes` unpaid domestic services + `33.27 minutes` own-use goods production.

For rural men aged 25-44:

- Weighted participation: `52,692,675 / 118,999,805 = 44.28%`.
- Unweighted diary count: `50,490`; unweighted participants: `22,996`.
- Weighted mean: `53.61 minutes/person/day`.

The 316.7-minute contrast is driven mainly by domestic services, not by a broad claim about every kind of production. This specificity is the lead finding.

### Strongest discovery B: a measurable firewood dependence marker

Among households with valid diary observation:

- Primary cooking fuel firewood/chips: `16,203,231.82 / 86,948,569.99 = 18.64%` had a diary member collect firewood; `n=36,690`.
- Other cooking fuels: `2,778,620.88 / 220,387,270.24 = 1.26%`; `n=102,793`.
- Ratio of weighted rates: about `14.8x`.

This directly connects a household characteristic to a resource-collection activity, while remaining an association rather than a causal effect.

### Strongest discovery C: state outlier with a visible gender contrast

In Arunachal Pradesh:

- Women: `407,552.32 / 451,421.52 = 90.28%` participation; `396.62 minutes/day`; `n=1,898`.
- Men: `311,883.22 / 487,851.50 = 63.93%`; `84.46 minutes/day`; `n=2,062`.
- Female-minus-male difference: `312.16 minutes/day = 5 h 12 m`.

Use this as a state-level descriptive outlier, not as evidence of statistical significance. Keep the unweighted samples visible in any graphic.

### Recommended visual concept

**Headline:** “The six-hour invisible shift”

**Subhead:** “Rural women aged 25-44 average 6 h 10 m a day in own-use goods production and unpaid domestic services; rural men of the same age average 54 minutes.”

Build the poster around the age-by-gender comparison, then use two supporting panels:

1. A four-bar rural/urban gender comparison separating own-use goods from unpaid domestic services.
2. A firewood dependence callout (18.64% vs 1.26%) and a state gap callout for Arunachal Pradesh.

Use the 242 water-collection statistic as a small “resource work” badge. Label each statistic as either participation, minutes/person/day, or minutes/participant/day; do not combine them in a single unlabeled scale.

## 5. Tables, charts, scripts, and reproduction

### Tables

- `person_group_estimates.csv` - detailed weighted subgroup estimates retained locally and excluded from the public repository; selected, less-granular summaries are published in the tables below.
- [`activity_estimates.csv`](../outputs/tables/activity_estimates.csv) - national activity-code estimates.
- [`gender_rural_urban.csv`](../outputs/tables/gender_rural_urban.csv) - gender and residence comparisons.
- [`state_comparison.csv`](../outputs/tables/state_comparison.csv) and [`state_gender_gaps.csv`](../outputs/tables/state_gender_gaps.csv) - state ranks and gaps.
- [`household_dependence.csv`](../outputs/tables/household_dependence.csv) - household-size, land, cooking-fuel, home-grown-consumption, sector, and state comparisons, with weighted numerators and denominators.
- [`major_only_sensitivity.csv`](../outputs/tables/major_only_sensitivity.csv) - official major-activity alternative.
- [`quality_checks.json`](../outputs/tables/quality_checks.json) and [`independent_validation.json`](../outputs/tables/independent_validation.json) - record/interval diagnostics and independent aggregation check.

### Charts

- [`gender_rural_urban.svg`](../outputs/charts/gender_rural_urban.svg)
- [`activity_times.svg`](../outputs/charts/activity_times.svg)
- [`state_comparison.svg`](../outputs/charts/state_comparison.svg)
- [`state_comparison_chart_only.svg`](../outputs/charts/state_comparison_chart_only.svg)
- [`largest_gender_gaps.svg`](../outputs/charts/largest_gender_gaps.svg)
- [`firewood_fuel_collection.svg`](../outputs/charts/firewood_fuel_collection.svg)

The SVGs are vector files suitable for scaling into a poster.

### Re-run

From the project directory:

```powershell
python analysis\extract_nesstar.py --overwrite
python analysis\analyze_tus.py --overwrite
python analysis\validate_results.py
```

The first two commands use `--overwrite` to replace only exports created by these scripts. The extraction script does not overwrite the source ZIP or `.Nesstar` container.

## 6. Limitations and interpretation

1. **Diary response and denominator:** the official NMDS reports 454,192 age-6+ people surveyed. The analysis denominator is 450,452 age-6+ people with a valid diary interval. It excludes 3,735 age-6+ roster people with no diary and five people with no valid interval. Response code 2 (“non-cooperative”) is included when a valid diary exists; a rural-women sensitivity check excluding it changed the combined mean from 266.23 to 266.25 minutes/person/day.
2. **Design-based uncertainty:** the official multiplier was applied, but no standard errors, confidence intervals, or significance tests were calculated. State and subgroup differences are weighted descriptive estimates only.
3. **Simultaneous activities:** equal allocation is the primary official-allowed treatment. Major-only results are provided separately; they are not interchangeable. The rural-women combined mean is 266.23 minutes/person/day under equal allocation and 276.27 under major-only.
4. **Household inference:** household rates mean “at least one valid diary member did the activity” among households with valid diary observation. Household-summed time rises with household size partly because more members can contribute diaries; it is not per-person time.
5. **Associations:** land, household size, primary fuel, and monthly imputed home-grown consumption value are descriptive correlates. None establishes cause or dependence by itself.
6. **Coverage and classification:** the DDI states that some difficult-to-access Andaman and Nicobar villages are outside survey coverage. Division 4 caregiving is intentionally excluded from this project's measure.
7. **Extraction and metadata differences:** a reverse-engineered reader was used for the binary Nesstar container, then checked against the DDI. Household record counts differ by two between the DDI/export (139,489) and the NMDS sample summary (139,487); the DDI also marks two `Household_Size` values invalid. Both counts are retained in the diagnostics.
8. **Household value proxy:** `Exp_Imputed_B` is a monthly imputed consumption value from home-grown stock. A positive value is not a measure of land, output volume, or causal reliance.

## Sources

- [Official MoSPI/NSSO TUS 2024 microdata catalogue](https://microdata.gov.in/NADA/index.php/catalog/236)
- [Official UNSD ICATUS 2016 table](https://unstats.un.org/unsd/demographic-social/time-use/icatus-2016/tableview)
- TUS 2024 DDI, survey README, NMDS 2.0 metadata, data-layout and field-code workbooks, sample-design procedure, Volume II, and instructions, available from the [official catalogue](https://microdata.gov.in/NADA/index.php/catalog/236). The DDI describes a stratified two-stage design with FSUs as villages/UFS blocks/sub-units and households as ultimate sampling units.
