"""Eunomia JSON -> 清洗后 JSON：字段映射、单位统一（℃ / MPa）、置信度过滤。

用法: python data_cleaner.py in.json out.json [--profile profiles/ffkm.yaml] [--min-confidence 0.6]
"""
import argparse
import json
import re

import yaml


def load_profile(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _celsius(m):
    n, u = float(m.group(1)), (m.group(2) or "C").upper()
    n = (n - 32) * 5 / 9 if u == "F" else (n - 273.15 if u == "K" else n)
    return f"{round(n, 1)}°C"


def _mpa(m):
    factor = {"psi": 0.00689476, "bar": 0.1, "kpa": 0.001, "mpa": 1.0}[m.group(2).lower()]
    return f"{round(float(m.group(1)) * factor, 3)} MPa"


def normalize(text):
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CFK])\b", _celsius, text, flags=re.I)
    return re.sub(r"(\d+(?:\.\d+)?)\s*(psi|bar|kPa|MPa)\b", _mpa, text, flags=re.I)


def clean_record(record, field_map, min_conf):
    if record.get("confidence") is not None and float(record["confidence"]) < min_conf:
        return None
    out = {}
    for key, value in record.items():
        mapped = field_map.get(key, key)
        out[mapped] = normalize(value) if isinstance(value, str) else value
    return out


def load_records(data):
    if isinstance(data, list):
        return data
    for key in ("records", "data", "results"):
        if isinstance(data.get(key), list):
            return data[key]
    return [data]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--profile", default="profiles/ffkm.yaml")
    parser.add_argument("--min-confidence", type=float, default=0.6)
    args = parser.parse_args()

    field_map = load_profile(args.profile).get("field_map", {})

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    cleaned = [
        item
        for item in (
            clean_record(record, field_map, args.min_confidence) for record in load_records(data)
        )
        if item
    ]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print(f"{len(cleaned)} records -> {args.output}")


if __name__ == "__main__":
    main()
