#!/usr/bin/env python3
"""Summarize Cat9kV web-tool audit JSONL records."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean


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
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def print_counter(title: str, counter: Counter) -> None:
    print(f"\n{title}")
    if not counter:
        print("  none")
        return
    for key, count in counter.most_common():
        print(f"  {key}: {count}")


def int_sum(records: list[dict], field: str) -> int:
    return sum(int(record.get(field) or 0) for record in records)


def print_recent_failures(failed: list[dict], limit: int = 5) -> None:
    print("\nRecent failures")
    if not failed:
        print("  none")
        return
    for record in failed[-limit:]:
        print(
            "  "
            f"{record.get('time', 'unknown')} | "
            f"{record.get('client_ip', 'unknown')} | "
            f"{record.get('esxi_host', 'unknown')} | "
            f"{record.get('mode', 'unknown')} | "
            f"{record.get('version', 'unknown')} | "
            f"{record.get('error', 'unknown error')}"
        )


def print_recent_deployments(records: list[dict], limit: int = 5) -> None:
    deployments = [record for record in records if record.get("mode") == "deploy" and record.get("status") == "success"]
    print("\nRecent successful deployments")
    if not deployments:
        print("  none")
        return
    for record in deployments[-limit:]:
        vm_names = ", ".join(record.get("vm_names") or [])
        print(
            "  "
            f"{record.get('time', 'unknown')} | "
            f"{record.get('esxi_host', 'unknown')} | "
            f"{record.get('version', 'unknown')} | "
            f"VMs={record.get('created_vm_count', 0)} | "
            f"{vm_names}"
        )


def main() -> int:
    path = Path(parse_args().audit_log)
    records = load_records(path)
    completed = [record for record in records if record.get("action") == "job_completed"]
    started = [record for record in records if record.get("action") == "job_started"]
    planned = [record for record in records if record.get("action") == "job_planned"]
    success = [record for record in completed if record.get("status") == "success"]
    failed = [record for record in completed if record.get("status") == "error"]
    dry_runs = [record for record in completed if record.get("mode") == "dry_run"]
    deployments = [record for record in completed if record.get("mode") == "deploy"]
    deploy_success = [record for record in deployments if record.get("status") == "success"]
    durations = [float(record.get("duration_seconds")) for record in completed if record.get("duration_seconds") is not None]
    console_checked = int_sum(success, "console_ports_checked")
    console_open = int_sum(success, "console_ports_open")

    print(f"Audit log: {path}")
    if records:
        print(f"First record: {records[0].get('time', 'unknown')}")
        print(f"Latest record: {records[-1].get('time', 'unknown')}")
    print(f"Job starts: {len(started)}")
    print(f"Job plans: {len(planned)}")
    print(f"Job completions: {len(completed)}")
    print(f"Successful completions: {len(success)}")
    print(f"Failed completions: {len(failed)}")
    print(f"Dry-run completions: {len(dry_runs)}")
    print(f"Deploy completions: {len(deployments)}")
    print(f"Successful deployments: {len(deploy_success)}")
    print(f"Unique client IPs: {len({record.get('client_ip') for record in records if record.get('client_ip')})}")
    print(f"Unique ESXi hosts: {len({record.get('esxi_host') for record in completed if record.get('esxi_host')})}")
    print(f"Total requested VMs: {int_sum(completed, 'requested_vm_count')}")
    print(f"Total planned VMs: {int_sum(success, 'planned_vm_count')}")
    print(f"Total VMs created: {int_sum(success, 'created_vm_count')}")
    print(f"Total network adapters disconnected: {int_sum(success, 'network_adapters_disconnected')}")
    if console_checked:
        print(f"Console ports open: {console_open}/{console_checked}")
    if durations:
        print(f"Average completion duration: {mean(durations):.1f}s")

    print_counter("By client IP", Counter(record.get("client_ip", "unknown") for record in completed))
    print_counter("By client kind", Counter(record.get("client_kind", "unknown") for record in completed))
    print_counter("By ESXi host", Counter(record.get("esxi_host", "unknown") for record in completed))
    print_counter("By selected datastore", Counter(record.get("selected_datastore", "unknown") for record in completed))
    print_counter("By selected resource pool", Counter(record.get("selected_resource_pool", "unknown") for record in completed))
    print_counter("By mode", Counter(record.get("mode", "unknown") for record in completed))
    print_counter("By version", Counter(record.get("version", "unknown") for record in completed))
    print_counter("By status", Counter(record.get("status", "unknown") for record in completed))
    print_counter("By error", Counter(record.get("error", "unknown") for record in failed))
    print_recent_failures(failed)
    print_recent_deployments(completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
