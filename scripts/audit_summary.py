#!/usr/bin/env python3
"""Summarize Cat9kV web-tool audit JSONL records."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


DEFAULT_AUDIT_LOG = "/opt/cat9kv-playbook/logs/audit.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Cat9kV deployment audit logs.")
    parser.add_argument("audit_log", nargs="?", default=DEFAULT_AUDIT_LOG)
    return parser.parse_args()


def load_records(path: Path) -> list[dict]:
    records = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def print_counter(title: str, counter: Counter) -> None:
    print(f"\n{title}")
    if not counter:
        print("  none")
        return
    for key, count in counter.most_common():
        print(f"  {key}: {count}")


def main() -> int:
    path = Path(parse_args().audit_log)
    records = load_records(path)
    completed = [record for record in records if record.get("action") == "job_completed"]
    started = [record for record in records if record.get("action") == "job_started"]
    success = [record for record in completed if record.get("status") == "success"]
    failed = [record for record in completed if record.get("status") == "error"]

    print(f"Audit log: {path}")
    print(f"Job starts: {len(started)}")
    print(f"Job completions: {len(completed)}")
    print(f"Successful completions: {len(success)}")
    print(f"Failed completions: {len(failed)}")
    print(f"Unique client IPs: {len({record.get('client_ip') for record in records if record.get('client_ip')})}")
    print(f"Total VMs created: {sum(int(record.get('created_vm_count') or 0) for record in success)}")

    print_counter("By client IP", Counter(record.get("client_ip", "unknown") for record in completed))
    print_counter("By ESXi host", Counter(record.get("esxi_host", "unknown") for record in completed))
    print_counter("By mode", Counter(record.get("mode", "unknown") for record in completed))
    print_counter("By version", Counter(record.get("version", "unknown") for record in completed))
    print_counter("By status", Counter(record.get("status", "unknown") for record in completed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
