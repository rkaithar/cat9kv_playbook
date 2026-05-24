# Cat9kV ESXi Playbook

This repository contains the basic Cat9kV-on-ESXi runbook and the initial configuration catalog for an automation engine.

The current scope is intentionally small:

1. Deploy a basic Cat9kV VM from the serial OVA or ISO.
2. Use a VM naming convention that includes the IOS-XE version token and serial console ports.
3. Discover datastore and ESXi inventory instead of assuming fixed names.
4. Leave port-group/interface mapping to the user after VM creation.
5. Ensure two network-backed serial ports even when an OVA does not include them.
6. Keep credentials out of files and prompt for them at runtime.

Start with [docs/cat9kv-esxi-runbook.md](docs/cat9kv-esxi-runbook.md).

The version catalog example is in [config/version-catalog.example.yaml](config/version-catalog.example.yaml). Copy it to a local runtime config when the automation engine is added.

To mirror images from the HTTP source at `10.76.90.102`, use [scripts/sync_cat9kv_images.py](scripts/sync_cat9kv_images.py):

```sh
python3 scripts/sync_cat9kv_images.py http://10.76.90.102/ --dest /srv/cat9kv/images --prune
```

The script only accepts files whose basename starts with `cat9kv-`, so Cat9K `.bin` images are not pulled into the Cat9kV image repository.

## Web Tool

The Ubuntu host can run the web interface with:

```sh
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8080
```

The deployed lab service is exposed through nginx at `http://10.76.90.60/`. The raw `/images/` directory listing is disabled, but direct image URLs remain available for automation.

The UI supports dry-run planning and deployment progress. Dry runs show a plain summary only. Completed deployments show clickable `telnet://` links, copy buttons for the telnet commands, and a link to the target ESXi UI for port-group changes.
