# Basic Cat9kV VM Deployment Runbook for ESXi

## Goal

Create and boot a basic Cat9kV VM on ESXi from the serial OVA or ISO, with only the minimum settings needed to reach the IOS console. Advanced Day-0 configuration, Catalyst Center onboarding, licensing, routing, VLANs, and detailed interface mapping are intentionally outside this basic runbook.

## Source Notes Used

This runbook was optimized from internal Cat9kV ESXi boot-up and know-how notes.

Key points from those notes:

1. Use the serial Cat9kV image, not the VGA image.
2. For ESXi, OVA is normally preferred. If OVA does not work or only ISO is available, create the VM manually and boot from ISO.
3. OVA deployment should keep the OVA-defined CPU, memory, disk, controller, CD/DVD, and NIC shape unless there is a known reason to override it.
4. ISO-based manual ESXi deployment should use at least `8 vCPU` and `16 GB RAM`.
5. Use thick disk provisioning for manual ISO deployment. The current working 17.18.03 OVA imports its disk as thin-provisioned, and that is acceptable for OVA-based deployment unless the lab requires thick disks.
6. Add two network-backed serial ports before boot:
   - First serial port: IOS console.
   - Second serial port: IOS-XE aux/Linux shell.
7. ESXi firewall must allow `VM serial port connected over network`.
8. Do not edit/remove ESXi network adapters casually after creation; Cat9kV interface mapping can be affected.
9. Datapath/platform selection through `vswitch.xml` ISO is optional for the basic boot path and should be a separate advanced option.
10. For the 17.18.03 OVA, use `govc import.ova` for import. A raw pyVmomi NFC upload hit an ESXi `403 File exists, but overwrite was not requested` error for the OVA-embedded ISO payload.

## Current Lab Defaults

| Item | Default |
| --- | --- |
| Preferred OVA HTTP source | `http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.ova` |
| ISO HTTP source | `http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.iso` |
| Upstream mirror source | `http://10.76.90.102/` |
| Datastore ISO path | `[<selected-datastore>] ISO/cat9kv-universalk9_serial.17.15.04.iso` |
| VM name pattern | `Cat9kv_<ios-version>_<serial1-port>_<serial2-port>` |
| Example VM name | `Cat9kv_171504_8021_8022` |
| Compatibility | `ESXi 8.0 U2 virtual machine` |
| Guest OS family | `Linux` |
| Guest OS version | `Other 3.x Linux (64-bit)` |
| Datastore | Discovered from ESXi; do not assume a fixed name |
| Port groups | Not changed by the basic automation |
| vCPU | `8` |
| Memory | `16 GB` |
| Disk | `16 GB`, thick provisioned lazy zeroed |
| CD/DVD | Datastore ISO, connected, connect at power on |
| Serial port 1 | `telnet://:8021`, connect at power on |
| Serial port 2 | `telnet://:8022`, connect at power on |

For OVA deployment, keep the OVA-defined hardware. The known-working 17.18.03 OVA on ESXi imported with `4 vCPU`, `18 GB RAM`, a `16 GB` thin disk, 9 x `E1000` NICs, and an OVA-provided CD/DVD device image on IDE. At the time of this update, `17.18.03` was not visible on the `10.76.90.102` HTTP listing, so it is not in the example catalog until the file server exposes it again.

## Image File Filter

The shared file server for this project must contain Cat9kV VM images only. Automated sync jobs must accept only files whose basename starts with:

```text
cat9kv-
```

Default allowed extensions are:

```text
.iso
.ova
```

Do not mirror Cat9K switch upgrade `.bin` files such as `cat9k_iosxe...bin`; those are not Cat9kV VM boot images.

Mirror command:

```sh
python3 scripts/sync_cat9kv_images.py http://10.76.90.102/ --dest /srv/cat9kv/images --prune
```

## VM Naming and Serial Port Convention

Use this format when creating the VM:

```text
Cat9kv_<ios-version>_<serial1-port>_<serial2-port>
```

Example:

```text
Cat9kv_171504_8021_8022
```

Rules:

1. `Cat9kv` identifies the VM family.
2. `171504` identifies IOS-XE `17.15.04`.
3. `8021` must match Serial Port 1 in the VM config.
4. `8022` must match Serial Port 2 in the VM config.
5. The script must validate that both ports are unused on the target ESXi host before creating the VM.
6. Generate the name before VM creation or OVA import. Renaming an existing VM later may not rename the VMX/VMDK files on the datastore.

This convention is safe for ESXi VM names. The main concern is drift: if someone changes serial ports later without renaming the VM, the name becomes misleading. The script should report the configured serial ports from the VM settings after creation.

## Mandatory Inputs for Script Runtime

These should be prompted at the start or accepted as CLI flags.

| Input | Handling |
| --- | --- |
| ESXi or vCenter IP/FQDN | Prompt. |
| Username | Prompt. |
| Password | Prompt with `getpass`; never log or save. |
| Target mode | Ask `standalone ESXi` or `vCenter`. |
| Number of Virtual Cat9k for this ESXi | Prompt. |
| Datastore | Discover and ask user to select. |
| ISO source/path | Default to current ISO, but verify it exists in datastore. |
| OVA source/path | Load from the version catalog and verify the URL before deployment. |

## Working OVA Automation Method

Use `govc` for OVA import, then use pyVmomi or the vSphere API to add the two serial ports and power on the VM.

Known-good flow on a standalone ESXi host:

1. Generate an import spec:

```sh
govc import.spec cat9kv-universalk9_serial.17.15.04.ova > import-spec.json
```

2. Edit the import spec:
   - Set `Name` to `Cat9kv_171504_<serial1>_<serial2>`.
   - Set `DiskProvisioning` to `thin`.
   - Set `PowerOn` to `false`.
   - Do not edit `NetworkMapping` for the basic engine.
   - Leave Day-0 `PropertyMapping` values blank for the basic runbook.

3. Import the OVA:

```sh
SELECTED_DATASTORE="<selected-datastore>"

govc import.ova \
  -options=import-Cat9kv_171504_8021_8022.json \
  -ds="$SELECTED_DATASTORE" \
  -pool='/ha-datacenter/host/localhost./Resources' \
  cat9kv-universalk9_serial.17.15.04.ova
```

4. Add two network-backed serial ports:

| Serial port | URI | Purpose |
| --- | --- | --- |
| Serial Port 1 | `telnet://:<serial1>` | IOS console |
| Serial Port 2 | `telnet://:<serial2>` | IOS-XE aux/Linux shell |

5. Power on the VM.

## Discover From ESXi/vCenter

After login, the script should fetch:

1. Host or cluster inventory.
2. Datastore names and free space.
3. Existing VM names.
4. Existing VM serial-port telnet URIs, where visible.
5. Whether the selected OVA/ISO already exists in the datastore or is reachable over HTTP.

## Optional Pre-Boot Inputs

These are not required for a basic VM boot, but must be handled before first boot if used.

| Optional item | When to use |
| --- | --- |
| Extra vNICs | Use if the user already knows the topology needs front-panel ports. Avoid adding/removing adapters later. |
| Platform/datapath profile | Use only when the user needs a specific Cat9kV personality/datapath. This may require a separate `vswitch.xml` config ISO. |
| `iosxe_config.txt` Day-0 ISO | Use only when the user wants automated IOS config injection. Keep out of script v1. |
| vSwitch security settings | For Cat9kV-to-Cat9kV traffic through an ESXi vSwitch, promiscuous mode, MAC changes, and forged transmits may need to be allowed on the relevant port groups. |

## Platform Profile Mapping

Keep this as an optional advanced selection until the exact `vswitch.xml` or platform injection mechanism is implemented in the script.

| Physical platform | Virtual platform |
| --- | --- |
| Cisco 9350 | `C9KV-A100L-8P` |
| Cisco 9550 | `C9KV-K100L-8P` |
| Catalyst 9300 | `C9KV-UADP-8P` |

## Basic Manual Procedure

### 1. Stage the Image

Prefer OVA deployment when practical. Keep the ISO available for manual ISO boot or fallback.

If the ISO is not already in the datastore:

```sh
ssh root@<esxi-host>
SELECTED_DATASTORE="<selected-datastore>"
mkdir -p "/vmfs/volumes/$SELECTED_DATASTORE/ISO"
wget -O "/vmfs/volumes/$SELECTED_DATASTORE/ISO/cat9kv-universalk9_serial.17.15.04.iso" \
  http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.iso
```

Expected datastore path:

```text
[<selected-datastore>] ISO/cat9kv-universalk9_serial.17.15.04.iso
```

### 2. Enable ESXi Serial Console Firewall

Enable `VM serial port connected over network` in the ESXi firewall.

CLI equivalent:

```sh
ssh root@<esxi-host>
esxcli network firewall ruleset set --enabled true --ruleset-id=remoteSerialPort
esxcli network firewall ruleset list | grep remoteSerialPort
```

### 3. Create VM

Create a new VM with:

| Setting | Value |
| --- | --- |
| Creation type | Create a new virtual machine |
| VM name | `Cat9kv_171504_<serial1-port>_<serial2-port>` |
| Compatibility | `ESXi 8.0 U2 virtual machine` |
| Guest OS family | `Linux` |
| Guest OS version | `Other 3.x Linux (64-bit)` |
| Datastore | Discovered datastore selected by the engine/user |
| CPU | `8` vCPU |
| Memory | `16 GB` |
| Hard disk | `16 GB`, thick provisioned lazy zeroed |
| Network adapter 1 | OVA/default network behavior; user can change port groups manually later |
| CD/DVD drive | Datastore ISO file, connected, connect at power on |

For OVA deployment, import the OVA and keep its hardware shape. Do not change OVA-created network mappings in the basic automation. Do not delete OVA-created NICs as part of a basic deployment.

### 4. Add Serial Ports

Add two serial ports before powering on.

| Serial port | Direction | URI | Purpose |
| --- | --- | --- | --- |
| Serial Port 1 | Server | `telnet://:8021` | IOS console |
| Serial Port 2 | Server | `telnet://:8022` | IOS-XE aux/Linux shell |

Use unique ports for each VM.

Example allocation:

| VM | Serial Port 1 | Serial Port 2 |
| --- | --- | --- |
| `Cat9kv_171504_8021_8022` | `8021` | `8022` |
| `Cat9kv_171504_8031_8032` | `8031` | `8032` |
| `Cat9kv_171504_8041_8042` | `8041` | `8042` |

### 5. Power On and Connect

Power on the VM, then connect to the IOS console:

```sh
telnet <esxi-management-ip> 8021
```

Optional aux/Linux shell:

```sh
telnet <esxi-management-ip> 8022
```

## Minimal Validation

From IOS console:

```ios
show version
show ip interface brief
show platform
show inventory
```

From ESXi/vSphere:

1. VM is powered on.
2. CPU is `8`.
3. Memory is `16 GB`.
4. ISO is connected at boot.
5. Serial ports are reachable by telnet.
6. Network adapters are present from the OVA. Port groups are not changed by the basic automation.

## Automation Engine Plan

Yes, this can be automated. The engine should be a Python CLI that uses:

1. `pyVmomi` for ESXi inventory, validation, serial-port configuration, power operations, and post-deploy checks.
2. `govc` for OVA import, because it reliably handles the OVA-embedded ISO payload.
3. SSH or the ESXi datastore browser API only when a standalone ISO must be staged manually.

The user should only need to provide:

| Input | Example |
| --- | --- |
| ESXi IP/FQDN | `<esxi-management-ip>` |
| ESXi username | `root` |
| ESXi password | Prompted with `getpass`; never saved |
| Cat9kV version | `17.15.04` |
| Number of Virtual Cat9k for this ESXi | `4` |

The engine should discover or auto-select:

| Item | Engine behavior |
| --- | --- |
| Datastore | Always discover datastores from ESXi first. If exactly one suitable datastore has enough free space, use it after showing the choice. If multiple suitable datastores exist, ask the user to select. Do not assume `datastore1` exists. |
| Port group | Do not ask and do not modify. Keep the OVA/default import network behavior. Print a post-deploy message telling the user to change ESXi port groups manually as required. |
| VM names | Generate from version and serial ports, for example `Cat9kv_171504_8021_8022` |
| Serial ports | Find unused pairs and embed them in the VM name |
| OVA/ISO URLs | Load from the local version catalog |
| OVA network mappings | Do not change in the basic engine. Keep OVA/default network mapping behavior. |
| Power-on | Default to power on after import |

### Version Catalog

Keep Cat9kV image information in a small local catalog so the user only selects a version.

```yaml
versions:
  "17.15.04":
    token: "171504"
    ova_url: "http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.ova"
    iso_url: "http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.iso"
    deployment_method: ova
    ova_keep_hardware_defaults: true
    serial_base: 8021
    serial_step: 10
```

### Serial Port Allocation

For each VM, allocate two ports:

```text
serial1 = base + index * step
serial2 = serial1 + 1
```

For version `17.15.04` with base `8021` and step `10`:

| Index | VM name | Serial 1 | Serial 2 |
| --- | --- | --- | --- |
| 0 | `Cat9kv_171504_8021_8022` | `8021` | `8022` |
| 1 | `Cat9kv_171504_8031_8032` | `8031` | `8032` |
| 2 | `Cat9kv_171504_8041_8042` | `8041` | `8042` |
| 3 | `Cat9kv_171504_8051_8052` | `8051` | `8052` |

Before deployment, the engine must scan all existing VMs for serial URIs and also attempt a TCP connect to the candidate ports. If either check shows a conflict, skip to the next pair.

### Engine Workflow

1. Prompt for ESXi IP/FQDN, username, password, Cat9kV version, and `Number of Virtual Cat9k for this ESXi`.
2. Connect to ESXi with pyVmomi.
3. Discover:
   - Existing VMs.
   - Used serial ports.
   - Datastores and free space.
   - ESXi firewall rule status for `remoteSerialPort`.
4. Select placement:
   - If one datastore has enough free space, show it and use it.
   - If multiple datastores have enough free space, ask the user to choose one.
   - If no datastore has enough free space, stop before import.
5. Validate:
   - Selected version exists in the version catalog.
   - OVA URL is reachable.
   - `govc` is installed.
   - Datastore has enough free space for `Number of Virtual Cat9k for this ESXi * OVA expanded size`.
   - Serial ports and VM names are free.
   - `remoteSerialPort` firewall rule is enabled.
6. Generate one `govc import.spec` file per VM:
   - Set the VM name.
   - Set `DiskProvisioning` to `thin` for the OVA unless the catalog overrides it.
   - Keep Day-0 `PropertyMapping` blank.
   - Do not alter OVA network mappings in the basic engine.
   - Keep `PowerOn` false during import.
7. Import each OVA with `govc import.ova`.
8. Add two network-backed serial ports with pyVmomi.
9. Power on each VM.
10. Verify:
   - VM exists and is powered on.
   - VM name matches serial-port config.
   - Serial TCP ports are reachable.
   - IOS console shows boot or initial config prompt.
11. Print a deployment summary with VM names and telnet commands.
12. Print: `Port groups were not changed by this automation. Update VM network adapter port groups manually in ESXi if your topology requires it.`

### Out of Scope for Basic Engine

Do not include these in the basic engine:

1. Catalyst Center onboarding.
2. License setup.
3. VLAN/routing/interface configuration.
4. Per-interface topology mapping.
5. Platform/datapath `vswitch.xml` generation.
6. Day-0 IOS configuration, unless explicitly enabled later.

Network interface mapping should not be changed by the basic engine. For the OVA path, import the OVA as-is and tell the user to update port groups manually after deployment if their topology requires it.

## Script Variable Template

```yaml
target:
  endpoint: <esxi-or-vcenter>
  mode: standalone_esxi
  host: <esxi-host>
vm:
  name: Cat9kv_171504_8021_8022
  family: Cat9kv
  ios_version: 17.15.04
  ios_version_token: "171504"
  deployment_method: ova_preferred
  compatibility: esxi8_0_u2
  guest_os_family: linux
  guest_os_version: other3x_linux_64
resources:
  ova_keeps_image_defaults: true
  iso_manual_vcpu: 8
  iso_manual_memory_gb: 16
  disk_gb: 16
  iso_manual_disk_format: thick_lazy_zeroed
storage:
  datastore: <selected-datastore>
  ova_http_url: http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.ova
  iso_http_url: http://10.76.90.60/images/cat9kv-universalk9_serial.17.15.04.iso
  iso_datastore_path: "[<selected-datastore>] ISO/cat9kv-universalk9_serial.17.15.04.iso"
network:
  modify_portgroups: false
  post_deploy_message: "Port groups were not changed by this automation. Update VM network adapter port groups manually in ESXi if your topology requires it."
serial:
  ports:
    - port: 8021
      uri: "telnet://:8021"
      purpose: ios_console
    - port: 8022
      uri: "telnet://:8022"
      purpose: iosxe_aux_linux_shell
advanced:
  platform_profile_enabled: false
  day0_config_enabled: false
```
