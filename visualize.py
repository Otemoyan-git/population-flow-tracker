"""visualize.py — 蓄積したCSVから人口移動トレンドのHTMLダッシュボードを生成する。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config
from migration_tracker import MigrationRecord, MunicipalityRecord, load_known, load_known_municipalities

TEMPLATE_PATH = Path(__file__).parent / "dashboard_template.html"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def build_payload(records: list[MigrationRecord]) -> dict:
    by_month: dict[str, dict[str, MigrationRecord]] = {}
    for r in records:
        by_month.setdefault(r.year_month, {})[r.pref_code] = r

    all_months = sorted(by_month.keys())
    latest_month = all_months[-1]
    window_months = all_months[-config.HEATMAP_MONTHS:]

    pref_order = list(config.PREF_NAMES.keys())

    heatmap_rows = []
    all_window_abs = []
    for code in pref_order:
        values = []
        for m in window_months:
            rec = by_month.get(m, {}).get(code)
            v = rec.net_migration if rec else None
            values.append(v)
            if v is not None:
                all_window_abs.append(abs(v))
        heatmap_rows.append({"code": code, "name": config.PREF_NAMES[code], "values": values})

    cap_abs = max(_percentile(all_window_abs, 0.90), 1)

    latest_records = by_month[latest_month]
    ranking = sorted(
        (
            {
                "code": code,
                "name": rec.pref_name,
                "in": rec.in_migrants,
                "out": rec.out_migrants,
                "net": rec.net_migration,
            }
            for code, rec in latest_records.items()
        ),
        key=lambda x: x["net"],
        reverse=True,
    )

    gainer_codes = [x["code"] for x in ranking[: config.TOP_N]]
    loser_codes = [x["code"] for x in ranking[-config.TOP_N :]][::-1]

    def series_for(codes: list[str]) -> list[dict]:
        out = []
        for code in codes:
            values = [
                (by_month.get(m, {}).get(code).net_migration if by_month.get(m, {}).get(code) else None)
                for m in window_months
            ]
            out.append({"code": code, "name": config.PREF_NAMES[code], "values": values})
        return out

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "latestMonth": latest_month,
        "months": window_months,
        "heatmap": {"capAbs": cap_abs, "rows": heatmap_rows},
        "gainers": series_for(gainer_codes),
        "losers": series_for(loser_codes),
        "ranking": ranking,
    }


def build_tokyo_payload(records: list[MunicipalityRecord]) -> dict:
    by_year: dict[str, dict[str, MunicipalityRecord]] = {}
    for r in records:
        by_year.setdefault(r.year, {})[r.muni_code] = r

    years = sorted(by_year.keys())
    latest_year = years[-1]

    latest_records = by_year[latest_year]
    ranking = sorted(
        (
            {
                "code": code,
                "name": rec.muni_name,
                "in": rec.in_migrants,
                "out": rec.out_migrants,
                "net": rec.net_migration,
            }
            for code, rec in latest_records.items()
        ),
        key=lambda x: x["net"],
        reverse=True,
    )

    cap_abs = max(_percentile([abs(x["net"]) for x in ranking], 0.90), 1)

    gainer_codes = [x["code"] for x in ranking[: config.TOKYO_TOP_N]]
    loser_codes = [x["code"] for x in ranking[-config.TOKYO_TOP_N :]][::-1]

    def series_for(codes: list[str]) -> list[dict]:
        out = []
        for code in codes:
            values = [
                (by_year.get(y, {}).get(code).net_migration if by_year.get(y, {}).get(code) else None)
                for y in years
            ]
            out.append({"code": code, "name": config.TOKYO_MUNICIPALITY_NAMES[code], "values": values})
        return out

    return {
        "latestYear": latest_year,
        "years": years,
        "rankingCapAbs": cap_abs,
        "ranking": ranking,
        "gainers": series_for(gainer_codes),
        "losers": series_for(loser_codes),
    }


def render_html(payload: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(payload, ensure_ascii=False)
    html = template.replace("__DATA_JSON__", data_json)
    html = html.replace("__LATEST_MONTH__", payload["latestMonth"])
    html = html.replace("__LATEST_TOKYO_YEAR__", payload["tokyo"]["latestYear"])
    html = html.replace("__GENERATED_AT__", payload["generatedAt"])
    return html


def main() -> None:
    records = list(load_known(Path(config.DATA_FILE)).values())
    if not records:
        raise RuntimeError(f"{config.DATA_FILE} にデータがありません。先に migration_tracker.py を実行してください。")

    tokyo_records = list(load_known_municipalities(Path(config.TOKYO_DATA_FILE)).values())
    if not tokyo_records:
        raise RuntimeError(f"{config.TOKYO_DATA_FILE} にデータがありません。先に migration_tracker.py を実行してください。")

    payload = build_payload(records)
    payload["tokyo"] = build_tokyo_payload(tokyo_records)
    html = render_html(payload)

    out_path = Path(config.OUTPUT_HTML)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"ダッシュボードを書き出しました: {out_path}")


if __name__ == "__main__":
    main()
