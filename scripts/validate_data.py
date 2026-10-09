#!/usr/bin/env python3
"""校验 data/election_dates.json：结构、字段、日期、来源链接与过期状态。

用法：
    python scripts/validate_data.py                 # 本地结构校验
    python scripts/validate_data.py --check-links   # 追加来源链接可达性抽检（联网，限速）
仅 Python 3.8+ 标准库。退出码 0=通过，1=有问题（可在 CI 中用作门禁）。
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "election_dates.json"
UA = "cn-voter-guide-maintainer/1.0 (+https://github.com/agarena/cn-voter-guide)"
DATE_RE = re.compile(r"^20\d{2}-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])$")
URL_RE = re.compile(r"^https?://\S+$")
REQUIRED_META = ("fetched_at", "expires_after", "coverage", "disclaimer")
REQUIRED_PROV = ("province", "window", "source", "source_type", "fetched_at")
REQUIRED_COUNTY = ("county", "province", "source", "source_type", "fetched_at")
SOURCE_TYPES = ("official", "repost")


def check_date(value: str):
    return bool(DATE_RE.match(value)) and _parseable(value)


def _parseable(value: str):
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check-links", action="store_true", help="抽检来源链接可达性（联网）")
    args = ap.parse_args()

    if not DATA_FILE.exists():
        sys.exit(f"未找到 {DATA_FILE}")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    problems, warnings = [], []

    meta = data.get("meta", {})
    for field in REQUIRED_META:
        if not meta.get(field):
            problems.append(f"meta 缺少字段 {field}")
    for field in ("fetched_at", "expires_after"):
        if meta.get(field) and not check_date(meta[field]):
            problems.append(f"meta.{field} 日期格式非法: {meta[field]}")

    today = dt.date.today()
    expires = meta.get("expires_after")
    if expires and check_date(expires):
        if today > dt.date.fromisoformat(expires):
            warnings.append(f"数据已过期（expires_after={expires}）：本轮换届已结束，时间数据仅作档案")
        else:
            left = (dt.date.fromisoformat(expires) - today).days
            print(f"时效：距过期还有 {left} 天（expires_after={expires}）")
    stale = meta.get("fetched_at")
    if stale and check_date(stale):
        age = (today - dt.date.fromisoformat(stale)).days
        if age > 30:
            warnings.append(f"fetched_at 已 {age} 天未更新，回答时间问题前应走联网检索核实")

    seen = set()
    for list_name, required in (("provinces", REQUIRED_PROV), ("counties", REQUIRED_COUNTY)):
        for i, entry in enumerate(data.get(list_name, [])):
            where = f"{list_name}[{i}]"
            for field in required:
                if not entry.get(field):
                    problems.append(f"{where} 缺少字段 {field}")
            if entry.get("source_type") not in SOURCE_TYPES:
                problems.append(f"{where} source_type 非法: {entry.get('source_type')}")
            if entry.get("source") and not URL_RE.match(entry["source"]):
                problems.append(f"{where} source 非法 URL: {entry['source']}")
            if entry.get("fetched_at") and not check_date(entry["fetched_at"]):
                problems.append(f"{where} fetched_at 日期非法: {entry['fetched_at']}")
            key = entry.get("province"), entry.get("county")
            if key in seen:
                problems.append(f"{where} 重复条目: {key}")
            seen.add(key)
            ed = entry.get("election_day")
            if list_name == "counties" and ed is not None:
                # 接受单日（2026-11-06）或区间（2026-08-20 至 2026-08-22，部分县分选区分日投票）
                parts = re.split(r"\s*至\s*", ed)
                if not (1 <= len(parts) <= 2 and all(check_date(p) for p in parts)):
                    problems.append(f"{where} election_day 日期非法: {ed}")

    if args.check_links:
        entries = data.get("provinces", []) + data.get("counties", [])
        for i, entry in enumerate(entries):
            url = entry.get("source")
            if not url:
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    code = resp.status
                status = f"HTTP {code}"
                if code >= 400:
                    problems.append(f"链接不可达({status}): {url}")
            except Exception as e:
                status = f"失败: {type(e).__name__}"
                warnings.append(f"链接异常({status}): {url}（政府站偶发 SSL/限流，人工浏览器复核后再定）")
            print(f"  链接[{i + 1}/{len(entries)}] {status} {url[:80]}")
            time.sleep(1.0)

    print(f"\n条目：省级 {len(data.get('provinces', []))} / 县级 {len(data.get('counties', []))}")
    for w in warnings:
        print(f"警告: {w}")
    for p in problems:
        print(f"问题: {p}")
    if problems:
        sys.exit(1)
    print("校验通过。")
    sys.exit(0)


if __name__ == "__main__":
    main()
