# Cat9kV ESXi Playbook

This repository contains the basic Cat9kV-on-ESXi runbook and the initial configuration catalog for an automation engine.

The current scope is intentionally small:

1. Deploy a basic Cat9kV VM from the serial OVA or ISO.
2. Use a VM naming convention that includes the IOS-XE version token and serial console ports.
3. Discover datastore and ESXi inventory instead of assuming fixed names.
4. Leave port-group/interface mapping to the user after VM creation.
5. Keep credentials out of files and prompt for them at runtime.

Start with [docs/cat9kv-esxi-runbook.md](docs/cat9kv-esxi-runbook.md).

The version catalog example is in [config/version-catalog.example.yaml](config/version-catalog.example.yaml). Copy it to a local runtime config when the automation engine is added.
