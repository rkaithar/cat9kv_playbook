from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from typing import Any
import uuid
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim
import yaml


APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
CATALOG_PATH = Path(os.environ.get("CAT9KV_CATALOG", REPO_DIR / "config/version-catalog.example.yaml"))
IMAGE_DIR = Path(os.environ.get("CAT9KV_IMAGE_DIR", "/srv/cat9kv/images"))
GOVC_BIN = os.environ.get("GOVC_BIN", "govc")
AUDIT_LOG = Path(os.environ.get("CAT9KV_AUDIT_LOG", REPO_DIR / "logs/audit.jsonl"))
SUPPORT_EMAIL = "rkaithar@cisco.com"
DEFAULT_RESOURCE_POOL = "/ha-datacenter/host/localhost./Resources"
RESOURCE_POOL_OVERRIDE = os.environ.get("CAT9KV_RESOURCE_POOL", "").strip()
AUDIT_SCHEMA_VERSION = 2


app = FastAPI(title="Cat9kV ESXi Deployment Tool")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()


class DeployRequest(BaseModel):
    esxi_host: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    version: str = Field(min_length=1, max_length=128)
    vm_count: int = Field(default=1, ge=1, le=20)
    mode: str = Field(default="dry_run", regex="^(dry_run|deploy)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise RuntimeError(f"catalog not found: {CATALOG_PATH}")
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    versions = data.get("versions")
    if not isinstance(versions, dict):
        raise RuntimeError("catalog is missing a versions map")
    return versions


def image_path_from_url(url: str) -> Path:
    filename = Path(urlparse(url).path).name
    if not filename.startswith("cat9kv-"):
        raise RuntimeError(f"image filename is not a Cat9kV image: {filename}")
    return IMAGE_DIR / filename


def make_job(client_ip: str) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "created_at": now_iso(),
            "client_ip": client_ip,
            "updated_at": now_iso(),
            "status": "queued",
            "phase": "Queued",
            "progress": 0,
            "events": [],
            "result": None,
            "error": None,
            "support_email": SUPPORT_EMAIL,
        }
    return job_id


def append_audit(record: dict[str, Any]) -> None:
    payload = {"time": now_iso(), **record}
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK:
        with AUDIT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def client_ip_from_request(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def trim_header(value: str | None, limit: int = 240) -> str:
    if not value:
        return ""
    return " ".join(value.split())[:limit]


def classify_client(user_agent: str) -> str:
    lower = user_agent.lower()
    if "curl/" in lower:
        return "curl"
    if "python" in lower or "httpx" in lower or "requests" in lower:
        return "python"
    if "mozilla/" in lower or "chrome/" in lower or "safari/" in lower or "firefox/" in lower:
        return "web_browser"
    if lower:
        return "automation"
    return "unknown"


def request_context_from_request(request: Request) -> dict[str, str]:
    user_agent = trim_header(request.headers.get("user-agent"))
    return {
        "client_ip": client_ip_from_request(request),
        "direct_client_ip": request.client.host if request.client and request.client.host else "unknown",
        "x_forwarded_for": trim_header(request.headers.get("x-forwarded-for")),
        "client_kind": classify_client(user_agent),
        "user_agent": user_agent,
        "origin": trim_header(request.headers.get("origin")),
        "referer": trim_header(request.headers.get("referer")),
    }


def audit_base(job_id: str, request: DeployRequest, request_context: dict[str, str]) -> dict[str, Any]:
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "job_id": job_id,
        **request_context,
        "esxi_host": request.esxi_host,
        "mode": request.mode,
        "version": request.version,
        "requested_vm_count": request.vm_count,
    }


def version_audit_details(version_cfg: dict[str, Any], local_ova: Path | None = None) -> dict[str, Any]:
    ova_name = Path(urlparse(str(version_cfg.get("ova_url", ""))).path).name
    iso_name = Path(urlparse(str(version_cfg.get("iso_url", ""))).path).name
    return {
        "version_token": version_cfg.get("token"),
        "deployment_method": version_cfg.get("deployment_method", "ova"),
        "ova_filename": ova_name,
        "iso_filename": iso_name,
        "local_ova": local_ova.name if local_ova else "",
        "disconnect_network_adapters": bool(version_cfg.get("disconnect_network_adapters", True)),
        "ensure_serial_ports": bool(version_cfg.get("ensure_serial_ports", True)),
    }


def inventory_audit_details(inventory: dict[str, Any]) -> dict[str, Any]:
    datastores = inventory.get("datastores", [])
    about = inventory.get("about", {})
    return {
        "esxi_product": about.get("full_name", ""),
        "esxi_version": about.get("version", ""),
        "esxi_build": about.get("build", ""),
        "datastore_count": len(datastores),
        "accessible_datastore_count": sum(1 for datastore in datastores if datastore.get("accessible")),
        "preexisting_vm_count": len(inventory.get("vm_names", [])),
        "used_serial_port_count": len(inventory.get("used_ports", [])),
    }


def datastore_audit_details(datastore: dict[str, Any] | None) -> dict[str, Any]:
    if not datastore:
        return {}
    return {
        "selected_datastore": datastore.get("name"),
        "selected_datastore_type": datastore.get("type"),
        "selected_datastore_capacity_gb": datastore.get("capacity_gb"),
        "selected_datastore_free_gb": datastore.get("free_gb"),
    }


def resource_pool_audit_details(resource_pool: str | None) -> dict[str, Any]:
    if not resource_pool:
        return {}
    return {"selected_resource_pool": resource_pool}


def planned_vms_audit(vms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": vm.get("name"),
            "serial1": vm.get("serial1"),
            "serial2": vm.get("serial2"),
        }
        for vm in vms
    ]


def deployed_vms_audit(vms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details = []
    for vm in vms:
        serial1_probe = vm.get("serial1_probe") or {}
        serial2_probe = vm.get("serial2_probe") or {}
        details.append({
            "name": vm.get("name"),
            "state": vm.get("state"),
            "serial1": vm.get("serial1"),
            "serial1_tcp_open": bool(serial1_probe.get("tcp_open")),
            "serial2": vm.get("serial2"),
            "serial2_tcp_open": bool(serial2_probe.get("tcp_open")),
            "network_adapters_disconnected": int(vm.get("network_adapters_disconnected") or 0),
        })
    return details


def console_probe_totals(vms: list[dict[str, Any]]) -> dict[str, int]:
    total = 0
    open_count = 0
    for vm in vms:
        for key in ("serial1_probe", "serial2_probe"):
            probe = vm.get(key)
            if not isinstance(probe, dict):
                continue
            total += 1
            if probe.get("tcp_open"):
                open_count += 1
    return {
        "console_ports_checked": total,
        "console_ports_open": open_count,
    }


def update_job(job_id: str, *, status: str | None = None, phase: str | None = None,
               progress: int | None = None, event: str | None = None,
               result: Any | None = None, error: str | None = None) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        if status is not None:
            job["status"] = status
        if phase is not None:
            job["phase"] = phase
        if progress is not None:
            job["progress"] = max(job.get("progress", 0), min(progress, 100))
        if event:
            job["events"].append({"time": now_iso(), "message": event})
            job["events"] = job["events"][-250:]
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        job["updated_at"] = now_iso()


def govc_env(esxi_host: str, username: str, password: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GOVC_URL": f"https://{esxi_host}/sdk",
        "GOVC_USERNAME": username,
        "GOVC_PASSWORD": password,
        "GOVC_INSECURE": "1",
    })
    return env


def run_govc(args: list[str], env: dict[str, str], *, timeout: int = 300) -> str:
    completed = subprocess.run(
        [GOVC_BIN, *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or f"govc {' '.join(args)} failed")
    return completed.stdout


def discover_resource_pool(env: dict[str, str]) -> str:
    if RESOURCE_POOL_OVERRIDE:
        return RESOURCE_POOL_OVERRIDE

    try:
        output = run_govc(["find", "/", "-type", "p"], env, timeout=120)
    except Exception:
        return DEFAULT_RESOURCE_POOL

    pools = [line.strip() for line in output.splitlines() if line.strip()]
    if DEFAULT_RESOURCE_POOL in pools:
        return DEFAULT_RESOURCE_POOL

    default_named = [pool for pool in pools if pool.rstrip("/").endswith("/Resources")]
    if default_named:
        return sorted(default_named, key=lambda pool: (pool.count("/"), pool))[0]
    if pools:
        return sorted(pools, key=lambda pool: (pool.count("/"), pool))[0]
    return DEFAULT_RESOURCE_POOL


def stream_govc(args: list[str], env: dict[str, str], job_id: str, *, timeout: int = 1800) -> str:
    output: list[str] = []
    process = subprocess.Popen(
        [GOVC_BIN, *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    start = time.monotonic()
    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        clean = " ".join(line.strip().split())
        if clean:
            update_job(job_id, event=clean[:240])
        if time.monotonic() - start > timeout:
            process.kill()
            raise RuntimeError(f"govc {' '.join(args)} timed out")
    code = process.wait()
    text = "".join(output)
    if code != 0:
        raise RuntimeError(text.strip() or f"govc {' '.join(args)} failed")
    return text


def connect_esxi(esxi_host: str, username: str, password: str):
    context = ssl._create_unverified_context()
    return SmartConnect(host=esxi_host, user=username, pwd=password, sslContext=context)


def wait_for_task(task, *, timeout: int = 180) -> None:
    start = time.monotonic()
    while task.info.state not in (vim.TaskInfo.State.success, vim.TaskInfo.State.error):
        if time.monotonic() - start > timeout:
            raise RuntimeError("ESXi task timed out")
        time.sleep(1)
    if task.info.state == vim.TaskInfo.State.error:
        error = task.info.error
        raise RuntimeError(getattr(error, "localizedMessage", str(error)))


def find_vm(si, vm_name: str):
    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            if vm.name == vm_name:
                return vm
    finally:
        view.Destroy()
    raise RuntimeError(f"VM not found after import: {vm_name}")


def get_inventory(si) -> dict[str, Any]:
    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    vms = list(view.view)
    view.Destroy()

    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
    datastores = []
    for datastore in view.view:
        summary = datastore.summary
        datastores.append({
            "name": summary.name,
            "type": summary.type,
            "capacity_gb": round(summary.capacity / 1024 ** 3, 1),
            "free_gb": round(summary.freeSpace / 1024 ** 3, 1),
            "accessible": bool(summary.accessible),
        })
    view.Destroy()

    used_ports: set[int] = set()
    vm_names: set[str] = set()
    for vm in vms:
        vm_names.add(vm.name)
        for device in vm.config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualSerialPort):
                port = parse_telnet_port(getattr(device.backing, "serviceURI", None))
                if port:
                    used_ports.add(port)

    return {
        "about": {
            "full_name": content.about.fullName,
            "version": content.about.version,
            "build": content.about.build,
            "api_type": content.about.apiType,
        },
        "vm_names": vm_names,
        "used_ports": used_ports,
        "datastores": datastores,
    }


def parse_telnet_port(uri: str | None) -> int | None:
    if not uri:
        return None
    match = re.search(r":(\d+)$", uri)
    return int(match.group(1)) if match else None


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def http_head(url: str) -> dict[str, Any]:
    request = UrlRequest(url, method="HEAD")
    with urlopen(request, timeout=10) as response:
        return {
            "status": response.status,
            "content_length": int(response.headers.get("Content-Length", "0") or 0),
        }


def select_datastore(datastores: list[dict[str, Any]], vm_count: int) -> dict[str, Any]:
    required_gb = max(25, vm_count * 25)
    candidates = [
        datastore for datastore in datastores
        if datastore["accessible"] and datastore["free_gb"] >= required_gb
    ]
    if not candidates:
        raise RuntimeError(f"No accessible datastore has at least {required_gb} GB free")
    return sorted(candidates, key=lambda item: item["free_gb"], reverse=True)[0]


def plan_vms(esxi_host: str, version_cfg: dict[str, Any], vm_count: int,
             existing_names: set[str], used_ports: set[int]) -> list[dict[str, Any]]:
    base = int(version_cfg["serial_base"])
    step = int(version_cfg.get("serial_step", 10))
    token = str(version_cfg["token"])
    planned: list[dict[str, Any]] = []
    index = 0
    while len(planned) < vm_count and index < 500:
        serial1 = base + index * step
        serial2 = serial1 + 1
        name = f"Cat9kv_{token}_{serial1}_{serial2}"
        has_conflict = (
            name in existing_names or
            serial1 in used_ports or
            serial2 in used_ports or
            port_open(esxi_host, serial1) or
            port_open(esxi_host, serial2)
        )
        if not has_conflict:
            planned.append({
                "name": name,
                "serial1": serial1,
                "serial2": serial2,
                "state": "planned",
            })
        index += 1
    if len(planned) != vm_count:
        raise RuntimeError("Unable to allocate enough unused serial port pairs")
    return planned


def console_summary(esxi_host: str, version: str, datastore: str, vms: list[dict[str, Any]]) -> str:
    lines = [
        "Cat9kV deployment summary",
        f"ESXi host: {esxi_host}",
        f"Version: {version}",
        f"Datastore: {datastore}",
        "",
    ]
    for vm in vms:
        lines.extend([
            f"VM: {vm['name']}",
            f"  Power state: {vm.get('state', 'planned')}",
            f"  Network adapters: {network_adapter_summary(vm)}",
            "  IOS console:",
            f"    telnet {esxi_host} {vm['serial1']}",
            "  Aux/Linux shell:",
            f"    telnet {esxi_host} {vm['serial2']}",
            "",
        ])
    lines.extend([
        "Post-deploy action:",
        "  Network adapters are disconnected by default to avoid ESXi L2 loops.",
        "  Map port groups and reconnect adapters manually in ESXi after topology review.",
    ])
    return "\n".join(lines)


def network_adapter_summary(vm: dict[str, Any]) -> str:
    count = vm.get("network_adapters_disconnected")
    if count is None:
        return "will be disconnected before first boot"
    return f"{count} disconnected; map/reconnect in ESXi when ready"


def write_import_spec(local_ova: Path, vm_name: str, env: dict[str, str]) -> Path:
    raw = run_govc(["import.spec", str(local_ova)], env, timeout=300)
    spec = json.loads(raw)
    spec["Name"] = vm_name
    spec["DiskProvisioning"] = "thin"
    spec["PowerOn"] = False
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(spec, handle, indent=2)
    return Path(handle.name)


def ensure_serial_ports(vm_name: str, serial1: int, serial2: int, env: dict[str, str]) -> None:
    output = run_govc(["device.ls", "-vm", vm_name], env, timeout=120)
    serial_devices = [
        line.split()[0] for line in output.splitlines()
        if line.startswith("serialport-")
    ]
    while len(serial_devices) < 2:
        run_govc(["device.serial.add", "-vm", vm_name], env, timeout=120)
        output = run_govc(["device.ls", "-vm", vm_name], env, timeout=120)
        serial_devices = [
            line.split()[0] for line in output.splitlines()
            if line.startswith("serialport-")
        ]
    run_govc(["device.serial.connect", "-vm", vm_name, "-device", serial_devices[0], f"telnet://:{serial1}"], env, timeout=120)
    run_govc(["device.serial.connect", "-vm", vm_name, "-device", serial_devices[1], f"telnet://:{serial2}"], env, timeout=120)


def disconnect_network_adapters(si, vm_name: str) -> list[str]:
    vm = find_vm(si, vm_name)
    device_changes = []
    disconnected: list[str] = []
    for device in vm.config.hardware.device:
        if not isinstance(device, vim.vm.device.VirtualEthernetCard):
            continue
        if device.connectable is None:
            device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
        device.connectable.connected = False
        device.connectable.startConnected = False

        change = vim.vm.device.VirtualDeviceSpec()
        change.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
        change.device = device
        device_changes.append(change)
        disconnected.append(device.deviceInfo.label)

    if device_changes:
        spec = vim.vm.ConfigSpec()
        spec.deviceChange = device_changes
        wait_for_task(vm.ReconfigVM_Task(spec))

    return disconnected


def probe_console(host: str, port: int) -> dict[str, Any]:
    result = {"port": port, "tcp_open": False, "sample": "", "error": None}
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            result["tcp_open"] = True
            sock.settimeout(2)
            chunks: list[bytes] = []
            try:
                data = sock.recv(4096)
                if data:
                    chunks.append(data)
            except socket.timeout:
                pass
            try:
                sock.sendall(b"\r\n")
            except OSError:
                pass
            time.sleep(0.5)
            try:
                data = sock.recv(4096)
                if data:
                    chunks.append(data)
            except socket.timeout:
                pass
            text = b"".join(chunks).decode("utf-8", errors="replace")
            result["sample"] = " ".join(text.split())[:240]
    except Exception as exc:
        result["error"] = str(exc)
    return result


def verify_vm(vm: dict[str, Any], esxi_host: str, env: dict[str, str]) -> dict[str, Any]:
    info = run_govc(["vm.info", vm["name"]], env, timeout=120)
    state = "poweredOn" if "Power state:  poweredOn" in info else "unknown"
    serial1 = probe_console(esxi_host, int(vm["serial1"]))
    serial2 = probe_console(esxi_host, int(vm["serial2"]))
    vm.update({
        "state": state,
        "serial1_probe": serial1,
        "serial2_probe": serial2,
    })
    return vm


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "invalidlogin" in lower or "incorrect user name or password" in lower:
        return "ESXi login failed. Verify the ESXi IP, username, and password"
    if "timed out" in lower or "timeout" in lower:
        return "The ESXi or image-server operation timed out"
    if "no accessible datastore" in lower:
        return text
    if "not available in the catalog" in lower or "ova is missing" in lower:
        return text
    return text


def run_job(job_id: str, request: DeployRequest, request_context: dict[str, str]) -> None:
    si = None
    started = time.monotonic()
    base_audit = audit_base(job_id, request, request_context)
    version_cfg: dict[str, Any] = {}
    local_ova: Path | None = None
    inventory: dict[str, Any] | None = None
    datastore: dict[str, Any] | None = None
    selected_resource_pool = ""
    planned_vms: list[dict[str, Any]] = []
    deployed: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    append_audit({
        **base_audit,
        "action": "job_started",
        "status": "started",
    })
    try:
        update_job(job_id, status="running", phase="Validating input", progress=5, event="Starting Cat9kV ESXi workflow")
        catalog = load_catalog()
        if request.version not in catalog:
            raise RuntimeError(f"Version {request.version} is not available in the catalog")
        version_cfg = catalog[request.version]
        local_ova = image_path_from_url(version_cfg["ova_url"])
        if not local_ova.exists():
            raise RuntimeError(f"OVA is missing on the image server: {local_ova.name}")

        update_job(job_id, phase="Connecting to ESXi", progress=12, event=f"Connecting to ESXi host {request.esxi_host}")
        si = connect_esxi(request.esxi_host, request.username, request.password)
        inventory = get_inventory(si)
        env = govc_env(request.esxi_host, request.username, request.password)
        update_job(job_id, event=f"Target ESXi: {inventory['about']['full_name']} build {inventory['about']['build']}")

        update_job(job_id, phase="Checking images and import metadata", progress=22, event="Checking local image URL and OVA metadata")
        http_head(version_cfg["ova_url"])
        http_head(version_cfg["iso_url"])
        run_govc(["import.spec", str(local_ova)], env, timeout=300)
        selected_resource_pool = discover_resource_pool(env)
        update_job(job_id, event=f"Selected resource pool {selected_resource_pool}")

        update_job(job_id, phase="Planning placement", progress=32, event="Selecting datastore and serial ports")
        datastore = select_datastore(inventory["datastores"], request.vm_count)
        planned_vms = plan_vms(
            request.esxi_host,
            version_cfg,
            request.vm_count,
            inventory["vm_names"],
            inventory["used_ports"],
        )
        update_job(job_id, event=f"Selected datastore {datastore['name']} with {datastore['free_gb']} GB free")
        append_audit({
            **base_audit,
            **version_audit_details(version_cfg, local_ova),
            **inventory_audit_details(inventory),
            **datastore_audit_details(datastore),
            **resource_pool_audit_details(selected_resource_pool),
            "action": "job_planned",
            "status": "success",
            "planned_vm_count": len(planned_vms),
            "planned_vms": planned_vms_audit(planned_vms),
            "vm_names": [vm["name"] for vm in planned_vms],
        })

        if request.mode == "dry_run":
            summary = console_summary(request.esxi_host, request.version, datastore["name"], planned_vms)
            append_audit({
                **base_audit,
                **version_audit_details(version_cfg, local_ova),
                **inventory_audit_details(inventory),
                **datastore_audit_details(datastore),
                **resource_pool_audit_details(selected_resource_pool),
                "action": "job_completed",
                "status": "success",
                "planned_vm_count": len(planned_vms),
                "created_vm_count": 0,
                "network_adapters_disconnected": 0,
                "planned_vms": planned_vms_audit(planned_vms),
                "vm_names": [vm["name"] for vm in planned_vms],
                "duration_seconds": round(time.monotonic() - started, 1),
            })
            update_job(job_id, status="completed", phase="Dry run complete", progress=100, event="Dry run completed without ESXi changes", result={
                "mode": request.mode,
                "esxi_host": request.esxi_host,
                "version": request.version,
                "datastore": datastore,
                "resource_pool": selected_resource_pool,
                "vms": planned_vms,
                "summary": summary,
            })
            return

        update_job(job_id, phase="Configuring ESXi", progress=38, event="Enabling remote serial port firewall rule")
        run_govc(["host.esxcli", "network", "firewall", "ruleset", "set", "-e", "true", "-r", "remoteSerialPort"], env, timeout=120)

        total = len(planned_vms)
        for index, vm in enumerate(planned_vms, start=1):
            update_job(job_id, phase="Copying OVA to ESXi", progress=40 + int((index - 1) * 35 / total), event=f"Importing {vm['name']}")
            spec_path = write_import_spec(local_ova, vm["name"], env)
            try:
                stream_govc(
                    [
                        "import.ova",
                        f"-options={spec_path}",
                        f"-ds={datastore['name']}",
                        f"-pool={selected_resource_pool}",
                        str(local_ova),
                    ],
                    env,
                    job_id,
                    timeout=2400,
                )
            finally:
                spec_path.unlink(missing_ok=True)

            update_job(job_id, phase="Configuring ESXi", progress=75 + int((index - 1) * 10 / total), event=f"Configuring serial ports for {vm['name']}")
            ensure_serial_ports(vm["name"], int(vm["serial1"]), int(vm["serial2"]), env)

            if version_cfg.get("disconnect_network_adapters", True):
                update_job(
                    job_id,
                    phase="Configuring ESXi",
                    progress=80 + int((index - 1) * 5 / total),
                    event=f"Disconnecting network adapters for {vm['name']}",
                )
                disconnected = disconnect_network_adapters(si, vm["name"])
                vm["network_adapters_disconnected"] = len(disconnected)
                vm["network_adapter_names"] = disconnected
            else:
                vm["network_adapters_disconnected"] = 0
                vm["network_adapter_names"] = []

            update_job(job_id, phase="Powering on VMs", progress=85 + int((index - 1) * 5 / total), event=f"Powering on {vm['name']}")
            run_govc(["vm.power", "-on", vm["name"]], env, timeout=180)
            deployed.append(vm)

        update_job(job_id, phase="Verifying serial consoles", progress=92, event="Checking serial console reachability")
        verified = [verify_vm(vm, request.esxi_host, env) for vm in deployed]
        summary = console_summary(request.esxi_host, request.version, datastore["name"], verified)
        append_audit({
            **base_audit,
            **version_audit_details(version_cfg, local_ova),
            **inventory_audit_details(inventory),
            **datastore_audit_details(datastore),
            **resource_pool_audit_details(selected_resource_pool),
            **console_probe_totals(verified),
            "action": "job_completed",
            "status": "success",
            "planned_vm_count": len(planned_vms),
            "created_vm_count": len(verified),
            "network_adapters_disconnected": sum(int(vm.get("network_adapters_disconnected") or 0) for vm in verified),
            "planned_vms": planned_vms_audit(planned_vms),
            "vm_details": deployed_vms_audit(verified),
            "vm_names": [vm["name"] for vm in verified],
            "duration_seconds": round(time.monotonic() - started, 1),
        })
        update_job(job_id, status="completed", phase="Completed", progress=100, event="Deployment completed", result={
            "mode": request.mode,
            "esxi_host": request.esxi_host,
            "version": request.version,
            "datastore": datastore,
            "resource_pool": selected_resource_pool,
            "vms": verified,
            "summary": summary,
        })
    except Exception as exc:
        message = friendly_error(exc)
        error_record = {
            **base_audit,
            "action": "job_completed",
            "status": "error",
            "planned_vm_count": len(planned_vms),
            "created_vm_count": len(deployed),
            "network_adapters_disconnected": sum(int(vm.get("network_adapters_disconnected") or 0) for vm in deployed),
            "planned_vms": planned_vms_audit(planned_vms),
            "vm_details": deployed_vms_audit(verified or deployed),
            "vm_names": [vm["name"] for vm in (verified or deployed or planned_vms)],
            "error": message,
            "duration_seconds": round(time.monotonic() - started, 1),
        }
        if version_cfg:
            error_record.update(version_audit_details(version_cfg, local_ova))
        if inventory:
            error_record.update(inventory_audit_details(inventory))
        if datastore:
            error_record.update(datastore_audit_details(datastore))
        if selected_resource_pool:
            error_record.update(resource_pool_audit_details(selected_resource_pool))
        if verified:
            error_record.update(console_probe_totals(verified))
        append_audit(error_record)
        update_job(
            job_id,
            status="error",
            phase="Error",
            progress=100,
            event=f"Error: {message}",
            error=f"{message}. Contact {SUPPORT_EMAIL} for further help.",
        )
    finally:
        if si is not None:
            Disconnect(si)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/versions")
def versions() -> dict[str, Any]:
    try:
        catalog = load_catalog()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "versions": [
            {
                "name": name,
                "token": cfg.get("token"),
                "deployment_method": cfg.get("deployment_method", "ova"),
            }
            for name, cfg in catalog.items()
        ]
    }


@app.post("/api/deploy")
def deploy(request_payload: DeployRequest, request: Request) -> dict[str, str]:
    request_context = request_context_from_request(request)
    job_id = make_job(request_context["client_ip"])
    thread = threading.Thread(target=run_job, args=(job_id, request_payload, request_context), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)
