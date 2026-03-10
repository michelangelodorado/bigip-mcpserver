# F5 BIG-IP MCP Server

MCP server for managing F5 BIG-IP devices through the iControl REST API.

## What it does

Provides 47 tools for full BIG-IP management:

- **System** — Device info, provisioning, failover status, config sync, save config, clock, DNS, NTP, licensing
- **LTM Virtual Servers** — List, get, create, modify, delete virtual servers
- **LTM Pools** — List, get, create, modify, delete pools
- **LTM Pool Members** — List, add, remove, enable/disable/force-offline members
- **LTM Nodes** — List, get, create, delete, enable/disable/force-offline nodes
- **LTM Monitors** — List and get health monitors by type
- **LTM iRules** — List and view iRule code
- **LTM Profiles & Persistence** — List profiles and persistence profiles by type
- **LTM Policies** — List and get policy rules
- **LTM Data Groups** — List and get internal data group records
- **LTM Other** — SNAT pools, virtual addresses, SSL certificates, SSL keys
- **Network** — VLANs, self IPs, routes, interfaces, trunks, route domains
- **GTM/DNS** — Wide IPs, GTM pools, data centers, GTM servers
- **Device Management** — Device groups, traffic groups
- **Utility** — Execute bash/tmsh commands on the BIG-IP

## Configuration

| Parameter | Required | Description |
|-----------|----------|-------------|
| `host` | Yes | BIG-IP management IP or hostname |
| `port` | No | Management port (default: 443) |
| `username` | Yes | BIG-IP username |
| `verify_ssl` | No | Verify SSL certificate (default: false) |

### Secrets

| Secret | Description |
|--------|-------------|
| `bigip.password` | BIG-IP password |

## Example usage

- "List all virtual servers on the BIG-IP"
- "Show me the members of pool /Common/web_pool and their status"
- "Disable pool member 10.0.0.5:80 in /Common/web_pool"
- "Create a new pool called /Common/api_pool with round-robin load balancing"
- "What is the failover status of this BIG-IP?"
- "Run tmsh command: show sys performance"
