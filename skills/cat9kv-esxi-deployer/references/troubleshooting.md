# Troubleshooting

## Authentication or Connection Failure

Report that ESXi validation failed without repeating the password. Ask the user to verify the ESXi address, account, password, API reachability, and account permissions. ESXi SSH does not need to be enabled.

## Version or Image Failure

Call `cat9kv_list_versions` and compare the requested version exactly. If the version exists but the server reports a missing OVA or ISO, report it as a deployment-server image problem rather than substituting another version.

## Placement Failure

The service discovers datastore and resource-pool paths. Report the selected values and the exact server error. Do not assume `datastore1`, `PG_Cluster`, or `/ha-datacenter/host/localhost./Resources`, and do not manually select a port group.

## Monitoring Timeout

`cat9kv_wait_for_job` can outlive an HTTP proxy timeout while the ESXi job continues. Query `cat9kv_get_job` using the original job ID. Do not call `cat9kv_start_deploy` again unless the original job is confirmed failed and the ESXi inventory has been checked for partially created VMs.

## Partial Deployment

If some VMs were created before an error, list them exactly as returned by the job or audit data. Do not delete, retry, or repair them without a new explicit request. A repeat deployment may allocate different names and serial ports or create duplicates.

## Serial Console Failure

For each VM, distinguish between VM creation success and serial TCP probe success. Report the expected commands:

```text
telnet <esxi-host> <serial1>
telnet <esxi-host> <serial2>
```

Check job events and audit data for the remote serial firewall rule, serial-port configuration, VM power state, and TCP probe results. Do not connect network adapters as a console troubleshooting step.

## Audit and Escalation

Use `cat9kv_audit_summary` for repeated or unexplained failures. Include the job ID, target ESXi host, selected version, failure phase, and sanitized error when escalating. Do not include credentials. For unresolved service failures, direct the user to `rkaithar@cisco.com`.
