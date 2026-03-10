# BIG-IP MCP Server

Model Context Protocol (MCP) server for F5 BIG-IP iControl REST API. Exposes BIG-IP management as MCP tools for use with AI assistants.

## Tools (47 total)

### System (10)
| Tool | Description |
|------|-------------|
| `get_device_info` | Device hostname, version, platform, serial, failover state |
| `get_system_provision` | Provisioned modules and levels |
| `get_failover_status` | HA failover status |
| `get_sync_status` | Config sync status |
| `sync_config` | Trigger config sync to a device group |
| `save_config` | Save running config to disk |
| `get_system_clock` | System date/time |
| `get_system_dns` | DNS nameservers and search domains |
| `get_system_ntp` | NTP servers and timezone |
| `get_system_license` | License and registration key info |

### LTM - Virtual Servers (5)
| Tool | Description |
|------|-------------|
| `list_virtual_servers` | List all virtual servers |
| `get_virtual_server` | Get VS details with profiles, policies |
| `create_virtual_server` | Create a new virtual server |
| `modify_virtual_server` | Modify VS destination, pool, state |
| `delete_virtual_server` | Delete a virtual server |

### LTM - Pools (5)
| Tool | Description |
|------|-------------|
| `list_pools` | List all pools |
| `get_pool` | Get pool details with members |
| `create_pool` | Create a new pool |
| `modify_pool` | Modify pool monitor, LB mode |
| `delete_pool` | Delete a pool |

### LTM - Pool Members (4)
| Tool | Description |
|------|-------------|
| `list_pool_members` | List members of a pool |
| `add_pool_member` | Add a member to a pool |
| `remove_pool_member` | Remove a member from a pool |
| `set_pool_member_state` | Enable/disable/force-offline a member |

### LTM - Nodes (5)
| Tool | Description |
|------|-------------|
| `list_nodes` | List all nodes |
| `get_node` | Get node details |
| `create_node` | Create a new node |
| `delete_node` | Delete a node |
| `set_node_state` | Enable/disable/force-offline a node |

### LTM - Monitors, iRules, Profiles, Policies, Data Groups (12)
| Tool | Description |
|------|-------------|
| `list_monitors` | List monitors by type |
| `get_monitor` | Get monitor configuration |
| `list_irules` | List all iRules |
| `get_irule` | Get iRule code |
| `list_profiles` | List profiles by type |
| `list_persistence_profiles` | List persistence profiles by type |
| `list_data_groups` | List internal data groups |
| `get_data_group` | Get data group records |
| `list_policies` | List LTM policies |
| `get_policy` | Get policy with rules |
| `list_snat_pools` | List SNAT pools |
| `list_virtual_addresses` | List virtual addresses |

### LTM - SSL (2)
| Tool | Description |
|------|-------------|
| `list_ssl_certificates` | List SSL certificates |
| `list_ssl_keys` | List SSL keys |

### Network (8)
| Tool | Description |
|------|-------------|
| `list_vlans` | List all VLANs |
| `get_vlan` | Get VLAN details with interfaces |
| `create_vlan` | Create a new VLAN |
| `delete_vlan` | Delete a VLAN |
| `list_self_ips` | List self IP addresses |
| `create_self_ip` | Create a self IP |
| `delete_self_ip` | Delete a self IP |
| `list_routes` | List static routes |
| `list_interfaces` | List network interfaces |
| `list_trunks` | List trunk groups |
| `list_route_domains` | List route domains |

### GTM / DNS (4)
| Tool | Description |
|------|-------------|
| `list_gtm_wideips` | List wide IPs by record type |
| `get_gtm_wideip` | Get wide IP details |
| `list_gtm_pools` | List GTM pools by record type |
| `list_gtm_datacenters` | List data centers |
| `list_gtm_servers` | List GTM servers |

### Device Management (2)
| Tool | Description |
|------|-------------|
| `list_device_groups` | List HA device groups |
| `list_traffic_groups` | List traffic groups |

### Utility (1)
| Tool | Description |
|------|-------------|
| `run_bash_command` | Execute bash/tmsh commands on BIG-IP |

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your BIG-IP credentials
```

### 2. Run with Docker

```bash
docker compose up --build
```

### 3. Run locally

```bash
pip install -r requirements.txt
python server.py
```

## MCP Client Configuration

### Claude Desktop / LibreChat (SSE)

```yaml
mcpServers:
  bigip:
    type: sse
    url: http://localhost:8000/sse
    timeout: 30000
```

### WebSocket

```
ws://localhost:8000/ws
```

## Test

```bash
pip install websockets httpx
python test_mcp.py localhost 8000
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BIGIP_HOST` | `192.168.1.245` | BIG-IP management IP or hostname |
| `BIGIP_PORT` | `443` | BIG-IP management port |
| `BIGIP_USERNAME` | `admin` | BIG-IP username |
| `BIGIP_PASSWORD` | `admin` | BIG-IP password |
| `BIGIP_VERIFY_SSL` | `false` | Verify SSL certificate |
| `MCP_PORT` | `8000` | MCP server listen port |
