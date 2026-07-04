"""migration_tracker.py — e-Statから都道府県別・月次の人口移動データを取得しCSVに蓄積する。"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path

import httpx

import config

API_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"


@dataclass
class MigrationRecord:
    year_month: str    # "2026-05"
    pref_code: str      # "13000"
    pref_name: str
    in_migrants: int    # 他都道府県からの転入者数
    out_migrants: int   # 他都道府県への転出者数
    net_migration: int  # 転入超過数
    fetched_at: str


def _time_code(year: int, month: int) -> str:
    return f"{year:04d}00{month:02d}{month:02d}"


def _parse_time_code(code: str) -> str:
    year = code[:4]
    month = code[-2:]
    return f"{year}-{month}"


def _backfill_from_code(years: int) -> str:
    now = datetime.now(timezone.utc)
    year, month = now.year - years, now.month
    return _time_code(year, month)


def _months_ago_code(months: int) -> str:
    now = datetime.now(timezone.utc)
    total = now.year * 12 + (now.month - 1) - months
    year, month = divmod(total, 12)
    return _time_code(year, month + 1)


# ── e-Stat API 取得 ──────────────────────────────────────────────────────

def _fetch_values(app_id: str, stats_data_id: str, extra_params: dict) -> list[dict]:
    params = {"appId": app_id, "statsDataId": stats_data_id, "limit": 100000, **extra_params}

    values: list[dict] = []
    start_position = None

    with httpx.Client(timeout=30) as client:
        while True:
            req_params = dict(params)
            if start_position:
                req_params["startPosition"] = start_position

            r = client.get(API_BASE, params=req_params)
            r.raise_for_status()
            body = r.json()["GET_STATS_DATA"]

            result = body["RESULT"]
            if result["STATUS"] != 0:
                raise RuntimeError(f"e-Stat API error: {result['ERROR_MSG']}")

            stat_data = body["STATISTICAL_DATA"]
            data_inf = stat_data["DATA_INF"]["VALUE"]
            if isinstance(data_inf, dict):
                data_inf = [data_inf]
            values.extend(data_inf)

            result_inf = stat_data["RESULT_INF"]
            to_number = result_inf["TO_NUMBER"]
            total_number = result_inf["TOTAL_NUMBER"]
            if to_number >= total_number:
                break
            start_position = to_number + 1

    return values


def fetch_migration(app_id: str, cd_time_from: str | None = None) -> list[MigrationRecord]:
    area_codes = ",".join(config.PREF_NAMES.keys())
    tab_codes = ",".join([config.TAB_IN_MIGRANTS, config.TAB_OUT_MIGRANTS, config.TAB_NET_MIGRATION])
    fetched_at = datetime.now(timezone.utc).isoformat()

    params = {
        "cdTab": tab_codes,
        "cdCat01": config.CAT01_TOTAL,
        "cdCat02": config.CAT02_TOTAL,
        "cdArea": area_codes,
    }
    if cd_time_from:
        params["cdTimeFrom"] = cd_time_from

    values = _fetch_values(app_id, config.STATS_DATA_ID, params)

    # (time, area) ごとに tab別の値を集約
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for v in values:
        key = (v["@time"], v["@area"])
        grouped.setdefault(key, {})[v["@tab"]] = int(v["$"])

    records: list[MigrationRecord] = []
    for (time_code, area_code), tabs in grouped.items():
        if config.TAB_IN_MIGRANTS not in tabs or config.TAB_OUT_MIGRANTS not in tabs:
            continue
        records.append(MigrationRecord(
            year_month=_parse_time_code(time_code),
            pref_code=area_code,
            pref_name=config.PREF_NAMES[area_code],
            in_migrants=tabs[config.TAB_IN_MIGRANTS],
            out_migrants=tabs[config.TAB_OUT_MIGRANTS],
            net_migration=tabs.get(config.TAB_NET_MIGRATION, tabs[config.TAB_IN_MIGRANTS] - tabs[config.TAB_OUT_MIGRANTS]),
            fetched_at=fetched_at,
        ))

    return records


# ── CSV 読み書き ───────────────────────────────────────────────────────────

_FIELD_NAMES = [f.name for f in fields(MigrationRecord)]


def load_known(path: Path) -> dict[tuple[str, str], MigrationRecord]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        result = {}
        for row in reader:
            row["in_migrants"] = int(row["in_migrants"])
            row["out_migrants"] = int(row["out_migrants"])
            row["net_migration"] = int(row["net_migration"])
            rec = MigrationRecord(**row)
            result[(rec.year_month, rec.pref_code)] = rec
        return result


def save_records(records: list[MigrationRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: (r.year_month, r.pref_code))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELD_NAMES)
        writer.writeheader()
        writer.writerows(asdict(r) for r in ordered)


# ── 東京都 市区町村別（年次） ────────────────────────────────────────────

@dataclass
class MunicipalityRecord:
    year: str          # "2025"
    muni_code: str      # "13113"
    muni_name: str
    in_migrants: int    # 他市町村からの転入者数
    out_migrants: int   # 他市町村への転出者数
    net_migration: int  # 転入超過数
    fetched_at: str


_MUNI_FIELD_NAMES = [f.name for f in fields(MunicipalityRecord)]


def fetch_tokyo_municipalities(app_id: str) -> list[MunicipalityRecord]:
    area_codes = ",".join(config.TOKYO_MUNICIPALITY_NAMES.keys())
    tab_codes = ",".join([config.TAB_CITY_IN, config.TAB_CITY_OUT, config.TAB_CITY_NET])
    fetched_at = datetime.now(timezone.utc).isoformat()

    params = {
        "cdTab": tab_codes,
        "cdCat01": config.CAT01_AGE_TOTAL,
        "cdCat02": config.CAT02_SEX_TOTAL,
        "cdCat03": config.CAT03_NATIONALITY_TOTAL,
        "cdArea": area_codes,
    }
    values = _fetch_values(app_id, config.TOKYO_STATS_DATA_ID, params)

    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for v in values:
        key = (v["@time"], v["@area"])
        grouped.setdefault(key, {})[v["@tab"]] = int(v["$"])

    records: list[MunicipalityRecord] = []
    for (time_code, area_code), tabs in grouped.items():
        if config.TAB_CITY_IN not in tabs or config.TAB_CITY_OUT not in tabs:
            continue
        records.append(MunicipalityRecord(
            year=time_code[:4],
            muni_code=area_code,
            muni_name=config.TOKYO_MUNICIPALITY_NAMES[area_code],
            in_migrants=tabs[config.TAB_CITY_IN],
            out_migrants=tabs[config.TAB_CITY_OUT],
            net_migration=tabs.get(config.TAB_CITY_NET, tabs[config.TAB_CITY_IN] - tabs[config.TAB_CITY_OUT]),
            fetched_at=fetched_at,
        ))

    return records


def load_known_municipalities(path: Path) -> dict[tuple[str, str], MunicipalityRecord]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        result = {}
        for row in reader:
            row["in_migrants"] = int(row["in_migrants"])
            row["out_migrants"] = int(row["out_migrants"])
            row["net_migration"] = int(row["net_migration"])
            rec = MunicipalityRecord(**row)
            result[(rec.year, rec.muni_code)] = rec
        return result


def save_municipality_records(records: list[MunicipalityRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: (r.year, r.muni_code))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_MUNI_FIELD_NAMES)
        writer.writeheader()
        writer.writerows(asdict(r) for r in ordered)


# ── エントリポイント ───────────────────────────────────────────────────────

def main(app_id: str, backfill: bool = False) -> list[MigrationRecord]:
    data_path = Path(config.DATA_FILE)
    known = load_known(data_path)
    print(f"既存レコード数: {len(known)}")

    if backfill or not known:
        cd_time_from = _backfill_from_code(config.BACKFILL_YEARS)
    else:
        cd_time_from = _months_ago_code(config.RECENT_MONTHS)
    fetched = fetch_migration(app_id, cd_time_from)
    print(f"取得レコード数: {len(fetched)}")

    for rec in fetched:
        known[(rec.year_month, rec.pref_code)] = rec

    all_records = list(known.values())
    save_records(all_records, data_path)
    print(f"保存後の総レコード数: {len(all_records)}")

    tokyo_path = Path(config.TOKYO_DATA_FILE)
    tokyo_known = load_known_municipalities(tokyo_path)
    tokyo_fetched = fetch_tokyo_municipalities(app_id)
    for rec in tokyo_fetched:
        tokyo_known[(rec.year, rec.muni_code)] = rec
    tokyo_all = list(tokyo_known.values())
    save_municipality_records(tokyo_all, tokyo_path)
    print(f"東京都市区町村データ件数: {len(tokyo_all)}")

    return all_records


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    main(os.environ["E_STAT_APP_ID"], backfill=True)
