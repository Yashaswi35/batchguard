"""Fetch 21 CFR Part 211 from the official eCFR API and save one record per section.

Usage: python data/fetch_regs.py
Writes regs/cfr_21_part_211.json (US federal regulations are public domain).
"""
import gzip
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "regs" / "cfr_21_part_211.json"
UA = {"User-Agent": "batchguard-portfolio/0.1 (research use)", "Accept": "*/*", "Accept-Encoding": "gzip"}


def get(url):
    # eCFR requires compressed responses (HTTP 406 otherwise), urllib does not decompress on its own
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        body = r.read()
        return gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body


def latest_date():
    titles = json.loads(get("https://www.ecfr.gov/api/versioner/v1/titles.json"))["titles"]
    return next(t["up_to_date_as_of"] for t in titles if t["number"] == 21)


def clean(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else latest_date()
    url = f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-21.xml?part=211"
    root = ET.fromstring(get(url))
    sections = []
    for div in root.iter():
        if div.attrib.get("TYPE") != "SECTION":
            continue
        head = div.find("HEAD")
        heading = clean("".join(head.itertext())) if head is not None else ""
        number = div.attrib.get("N", "").replace("§", "").strip()
        paras = [clean("".join(p.itertext())) for p in div if p.tag != "HEAD"]
        text = "\n".join(p for p in paras if p)
        if number and text:
            sections.append(dict(section=number, heading=heading, text=text,
                                 source_url=f"https://www.ecfr.gov/current/title-21/section-{number}",
                                 as_of=date))
    if len(sections) < 30:
        raise SystemExit(f"Only parsed {len(sections)} sections, eCFR XML format may have changed")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sections, indent=1, ensure_ascii=False))
    print(f"saved {len(sections)} sections of 21 CFR 211 as of {date} -> {OUT.relative_to(REPO)}")
    for s in sections:
        if s["section"] in ("211.63", "211.67", "211.68", "211.100", "211.160", "211.192"):
            print(f"  {s['section']}: {s['heading'][:70]} ({len(s['text'])} chars)")


if __name__ == "__main__":
    main()
