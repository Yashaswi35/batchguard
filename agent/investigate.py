"""CLI: python -m agent.investigate 92 --llm rule|gemini|replay"""
import argparse
import json

from pipeline import config as C

from .audit import AuditLog
from .graph import build_graph, investigate
from .llm import get_llm
from .tools import GoldStore, RegRetriever

REGS = C.REPO / "regs" / "cfr_21_part_211.json"


def make_app(llm_kind="rule", regs_path=REGS, audit_path=None):
    return build_graph(GoldStore(C.LAKE), RegRetriever(regs_path), get_llm(llm_kind),
                       AuditLog(audit_path or C.DATA_DIR / "audit.db"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("batch_id")
    p.add_argument("--llm", default="rule", choices=["rule", "gemini", "record", "replay"])
    a = p.parse_args()
    bid = int(a.batch_id) if a.batch_id.lstrip("-").isdigit() else a.batch_id
    print(json.dumps(investigate(make_app(a.llm), bid), indent=1))


if __name__ == "__main__":
    main()
