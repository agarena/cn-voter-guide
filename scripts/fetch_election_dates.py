#!/usr/bin/env python3
"""按 data/sources.json 白名单抓取种子站点的选举公告线索。

产出的是"线索"（含关键词与日期的公告标题/正文片段），写入 data/findings/，
供维护者人工核实后合入 data/election_dates.json——不自动改正式数据文件，
这是内容准确性纪律（每条日期必须有可访问的官方出处）的一部分。

采集纪律（内置于代码）：限速 1 请求/秒；遵守 robots 协议；只抓列表页；
User-Agent 标明用途与仓库地址。

用法：
    python scripts/fetch_election_dates.py                 # 全部源
    python scripts/fetch_election_dates.py --source 隆回   # 按名称过滤
仅 Python 3.8+ 标准库，无第三方依赖。
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.robotparser as robotparser
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "data" / "sources.json"
FINDINGS_DIR = ROOT / "data" / "findings"

UA = "cn-voter-guide-maintainer/1.0 (+https://github.com/aipanini/cn-voter-guide; polite, 1req/s)"
KEYWORDS = ("选举日", "选举委员会公告", "选民登记", "当选", "代表资格", "候选人名单", "换届")
DATE_RE = re.compile(r"20\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?")
REQUEST_INTERVAL = 1.0  # 秒


class _TextExtractor(HTMLParser):
    """抽取 (链接文本, href) 与纯文本行，忽略 script/style。"""

    def __init__(self):
        super().__init__()
        self.links, self.text_lines, self._buf = [], [], []
        self._href, self._skip, self._line = None, 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4"):
            self._flush_line()

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = "".join(self._line).strip()
            if text:
                self.links.append((text, self._href))
            self._href = None
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._flush_line()

    def handle_data(self, data):
        if not self._skip:
            self._line.append(data)

    def _flush_line(self):
        line = re.sub(r"\s+", " ", "".join(self._line)).strip()
        if line:
            self.text_lines.append(line)
        self._line = []

    def result(self):
        self._flush_line()
        return self.links, self.text_lines


def decode_html(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def robots_allows(url: str) -> bool:
    try:
        parts = urllib.request.urlparse(url)
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True  # robots 不可得（超时/404）按行业惯例允许抓取，限速仍然生效


def fetch(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return decode_html(resp.read()), resp.geturl()


def extract_leads(html: str, base_url: str):
    links, lines = _run_extractor(html)
    leads = []
    seen = set()
    for text, href in links:
        if any(k in text for k in KEYWORDS):
            entry = {
                "title": text[:120],
                "url": urljoin(base_url, href),
                "dates_in_title": DATE_RE.findall(text),
            }
            key = entry["url"]
            if key not in seen:
                seen.add(key)
                leads.append(entry)
    body_hits = [
        {"snippet": line[:200], "dates": DATE_RE.findall(line)}
        for line in lines
        if any(k in line for k in KEYWORDS) and DATE_RE.search(line)
    ][:40]
    return leads, body_hits


def _run_extractor(html: str):
    p = _TextExtractor()
    p.feed(html)
    return p.result()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", help="按名称关键字过滤白名单")
    args = ap.parse_args()

    if not SOURCES_FILE.exists():
        sys.exit(f"未找到 {SOURCES_FILE}")
    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]
    if args.source:
        sources = [s for s in sources if args.source in s["name"]]
    if not sources:
        sys.exit("没有匹配的源")

    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    report = {"fetched_at": today, "ok": 0, "failed": 0, "results": []}

    for s in sources:
        print(f"[{s['level']:>8}] {s['name']} -> {s['url']}")
        try:
            if not robots_allows(s["url"]):
                raise RuntimeError("robots.txt 不允许")
            html, final_url = fetch(s["url"])
            leads, body_hits = extract_leads(html, final_url)
            report["ok"] += 1
            status = f"线索 {len(leads)} 条 / 含日期正文行 {len(body_hits)} 行"
        except Exception as e:
            leads, body_hits, status = [], [], f"失败: {e}"
            report["failed"] += 1
        print(f"          {status}")
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", s["name"]).strip("-")
        (FINDINGS_DIR / f"{slug}-{today}.json").write_text(
            json.dumps(
                {"source": s, "status": status, "leads": leads, "body_hits": body_hits},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report["results"].append({"name": s["name"], "status": status, "leads": len(leads)})
        time.sleep(REQUEST_INTERVAL)

    (FINDINGS_DIR / f"_summary-{today}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成：成功 {report['ok']} / 失败 {report['failed']}。线索文件在 {FINDINGS_DIR}")
    print("下一步：人工核实线索 → 合入 data/election_dates.json → python scripts/validate_data.py")


if __name__ == "__main__":
    main()
