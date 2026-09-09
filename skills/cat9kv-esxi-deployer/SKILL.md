---
name: cat9kv-esxi-deployer
description: Plan, deploy, monitor, and troubleshoot Cat9kV virtual switches on ESXi through the Cat9kV MCP service. Use for Cat9kV image discovery, deployment dry-runs, VM creation, serial-console verification, or deployment audit checks; do not use for general ESXi VM administration.
---

# Cat9kV ESXi Deployer

Use the Cat9kV MCP tools for deployment operations. Do not bypass the service with direct ESXi SSH, `govc`, or REST calls because the service owns resource discovery, serial-port allocation, network safety, and auditing.

## Inputs

Collect only the values needed for the requested operation:

- ESXi IP address or FQDN
- ESXi username and password
- Cat9kV version
- Number of VMs

Use `cat9kv_list_versions` when the version is missing, ambiguous, or needs validation. Pass credentials only to the deployment MCP calls. Do not write them to files, include them in summaries, or repeat the password back to the user.

## Workflow

1. Confirm that the Cat9kV MCP tools are available. If they are unavailable, provide `codex mcp add cat9kv --url http://10.76.90.60/mcp` and stop before attempting deployment another way.
2. Run `cat9kv_dry_run` before every deployment. For a planning-only request, return the dry-run result and stop.
3. Check that the plan matches the requested ESXi host, version, and VM count. Report the selected datastore and resource pool, planned VM names, and serial-port pairs.
4. Call `cat9kv_start_deploy` only when the user explicitly requested VM creation. A request such as "create" or "deploy" is deployment authorization; do not require a second confirmation when the dry-run exactly matches it and reports no error.
5. Monitor the returned job ID with `cat9kv_wait_for_job` or `cat9kv_get_job` until the job completes or fails. Treat a wait timeout or gateway timeout as an uncertain monitoring result, not a failed deployment; query the job before considering any retry.
6. On success, report the ESXi host, version, datastore, resource pool, VM names, disconnected network-adapter counts, and both `telnet <esxi-host> <port>` commands for every VM. State clearly when a console probe is not reachable.
7. On failure, report the job error and obtain `cat9kv_audit_summary` when it will help identify a server-side pattern. Do not restart a failed or uncertain deployment automatically because some VMs may already exist.

Read [references/deployment-policy.md](references/deployment-policy.md) before authorizing or reviewing a deployment. Read [references/troubleshooting.md](references/troubleshooting.md) when a job fails, stalls, or returns an uncertain result.

## Boundaries

- Do not create, delete, power-cycle, rename, or reconfigure existing VMs outside the returned deployment job.
- Never modify `Catc_VM` on ESXi `10.76.90.41`; it is a production VM.
- Do not create, select, or change port groups. The service preserves imported mappings and disconnects Ethernet adapters before first boot.
- Do not claim console success unless the completed job reports successful TCP probes.
- Do not expose credentials in commentary, final responses, logs, or audit queries.
