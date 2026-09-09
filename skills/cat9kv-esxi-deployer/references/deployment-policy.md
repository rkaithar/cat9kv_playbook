# Deployment Policy

## Service-Owned Decisions

The Cat9kV deployment service, not the skill, performs these operations:

- Validate the selected version and confirm that its OVA and ISO are available.
- Connect to ESXi through the VMware API; ESXi SSH is not required.
- Discover the ESXi inventory, datastore, and resource pool instead of assuming fixed names.
- Select a datastore with sufficient free space.
- Allocate unused serial-port pairs and reject VM-name or TCP-port conflicts.
- Import the OVA, ensure two network-backed serial ports, and enable the ESXi `remoteSerialPort` firewall ruleset.
- Preserve the OVA's port-group mappings while setting every Ethernet adapter to disconnected and not connected at power-on.
- Power on new VMs, probe both serial-console ports, and write audit records.

Do not reproduce these decisions in the skill or infer replacements for values returned by the service.

## Plan Review

Before deployment, verify that the dry-run result contains:

- The requested ESXi host and Cat9kV version.
- The requested number of planned VMs.
- A discovered datastore and resource pool.
- Unique VM names and two unique serial ports per VM.
- VM names in the form `Cat9kv_<version-token>_<serial1>_<serial2>`.

Stop and explain the mismatch when any requested value differs. Do not override placement or serial allocation manually.

## Authorization

A request that explicitly says to create, deploy, push, or spin up VMs authorizes deployment after a matching dry-run. A request to inspect, check feasibility, plan, compare, or dry-run does not authorize VM creation.

The dry-run contacts ESXi and records an audit event but does not create VMs. Deployment changes ESXi and must remain within the host, version, and VM count authorized by the user.

## Network Safety

New Cat9kV VMs must start with all Ethernet adapters disconnected to avoid accidental Layer 2 loops. Port-group mapping remains available for the user to review and connect later in ESXi. A deployment result that indicates zero disconnected adapters is not automatically wrong, but call it out for review when the imported OVA contains network adapters.
