"""
BIG-IP MCP Server
Model Context Protocol server for F5 BIG-IP iControl REST API.
Exposes LTM, Network, System, GTM, and Utility management as MCP tools.
"""

import os
import json
import uuid
import asyncio
import logging
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("bigip-mcp")

# ── BIG-IP Configuration ─────────────────────────────────────────────
BIGIP_HOST = os.environ.get("BIGIP_HOST", "192.168.1.245")
BIGIP_USER = os.environ.get("BIGIP_USERNAME", "admin")
BIGIP_PASS = os.environ.get("BIGIP_PASSWORD", "admin")
BIGIP_PORT = os.environ.get("BIGIP_PORT", "443")
VERIFY_SSL = os.environ.get("BIGIP_VERIFY_SSL", "false").lower() == "true"
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

BASE_URL = f"https://{BIGIP_HOST}:{BIGIP_PORT}/mgmt"

SERVER_INFO = {
    "name": "bigip-mcp",
    "version": "1.0.0",
}


# ── HTTP Client ───────────────────────────────────────────────────────
def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        auth=(BIGIP_USER, BIGIP_PASS),
        verify=VERIFY_SSL,
        timeout=30.0,
        headers={"Content-Type": "application/json"},
    )


# ── Helpers ───────────────────────────────────────────────────────────
def _to_path(name: str) -> str:
    """Convert '/Common/my_vs' or 'Common/my_vs' to '~Common~my_vs'."""
    if name.startswith("/"):
        name = name[1:]
    return name.replace("/", "~")


def _strip_meta(items: list[dict]) -> list[dict]:
    for item in items:
        for key in ("selfLink", "kind", "generation"):
            item.pop(key, None)
    return items


async def _get(path: str, params: dict | None = None) -> Any:
    async with _client() as c:
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, payload: dict) -> Any:
    async with _client() as c:
        r = await c.post(path, json=payload)
        r.raise_for_status()
        return r.json()


async def _patch(path: str, payload: dict) -> Any:
    async with _client() as c:
        r = await c.patch(path, json=payload)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> str:
    async with _client() as c:
        r = await c.delete(path)
        r.raise_for_status()
        return "deleted"


async def _put(path: str, payload: dict) -> Any:
    async with _client() as c:
        r = await c.put(path, json=payload)
        r.raise_for_status()
        return r.json()


# ── Tool Registry ────────────────────────────────────────────────────
TOOLS: list[dict] = []
HANDLERS: dict[str, Any] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None):
    def decorator(fn):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        })
        HANDLERS[name] = fn
        return fn
    return decorator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SYSTEM TOOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "get_device_info",
    "Get BIG-IP device information: hostname, version, platform, serial number, management IP, failover state, and active modules.",
)
async def get_device_info(args):
    data = await _get("/tm/cm/device")
    devices = data.get("items", [])
    return [
        {k: d.get(k) for k in (
            "name", "hostname", "version", "build", "edition",
            "platformId", "chassisId", "managementIp",
            "failoverState", "configsyncIp", "activeModules",
            "marketingName",
        ) if d.get(k) is not None}
        for d in devices
    ]


@tool(
    "get_system_provision",
    "List provisioned modules and their levels (e.g. nominal, dedicated, minimum).",
)
async def get_system_provision(args):
    data = await _get("/tm/sys/provision")
    return [
        {"name": i["name"], "level": i.get("level", "none")}
        for i in data.get("items", [])
        if i.get("level") not in (None, "none")
    ]


@tool(
    "get_failover_status",
    "Get the high-availability failover status of the BIG-IP device.",
)
async def get_failover_status(args):
    data = await _get("/tm/cm/failover-status")
    entries = data.get("entries", {})
    for entry in entries.values():
        nested = entry.get("nestedStats", {}).get("entries", {})
        status = nested.get("status", {}).get("description", "unknown")
        color = nested.get("color", {}).get("description", "unknown")
        return {"status": status, "color": color}
    return {"status": "unknown"}


@tool(
    "get_sync_status",
    "Get the configuration sync status across the device group.",
)
async def get_sync_status(args):
    data = await _get("/tm/cm/sync-status")
    entries = data.get("entries", {})
    for entry in entries.values():
        nested = entry.get("nestedStats", {}).get("entries", {})
        status = nested.get("status", {}).get("description", "unknown")
        color = nested.get("color", {}).get("description", "unknown")
        mode = nested.get("mode", {}).get("description", "unknown")
        return {"status": status, "color": color, "mode": mode}
    return {"status": "unknown"}


@tool(
    "sync_config",
    "Trigger a configuration sync to a specified device group.",
    properties={
        "device_group": {
            "type": "string",
            "description": "Name of the device group to sync (e.g. 'device-group-1').",
        },
    },
    required=["device_group"],
)
async def sync_config(args):
    payload = {
        "command": "run",
        "utilCmdArgs": f"config-sync to-group {args['device_group']}",
    }
    await _post("/tm/cm", payload)
    return {"result": f"Config sync triggered to group '{args['device_group']}'"}


@tool(
    "save_config",
    "Save the running configuration to disk (equivalent to 'save sys config').",
)
async def save_config(args):
    payload = {"command": "save"}
    await _post("/tm/sys/config", payload)
    return {"result": "Configuration saved successfully"}


@tool(
    "get_system_clock",
    "Get the current system date and time from the BIG-IP.",
)
async def get_system_clock(args):
    data = await _get("/tm/sys/clock")
    entries = data.get("entries", {})
    for entry in entries.values():
        nested = entry.get("nestedStats", {}).get("entries", {})
        desc = nested.get("fullDate", {}).get("description", "unknown")
        return {"datetime": desc}
    return {"datetime": "unknown"}


@tool(
    "get_system_dns",
    "Get the DNS configuration (nameservers and search domains).",
)
async def get_system_dns(args):
    data = await _get("/tm/sys/dns")
    return {
        "nameServers": data.get("nameServers", []),
        "search": data.get("search", []),
    }


@tool(
    "get_system_ntp",
    "Get NTP server configuration.",
)
async def get_system_ntp(args):
    data = await _get("/tm/sys/ntp")
    return {
        "servers": data.get("servers", []),
        "timezone": data.get("timezone", ""),
    }


@tool(
    "get_system_license",
    "Get BIG-IP license information including registration key and licensed modules.",
)
async def get_system_license(args):
    data = await _get("/tm/sys/license")
    entries = data.get("entries", {})
    for entry in entries.values():
        nested = entry.get("nestedStats", {}).get("entries", {})
        reg_key_ref = nested.get("registrationKey", {})
        return nested
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – VIRTUAL SERVERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_virtual_servers",
    "List all LTM virtual servers. Returns name, destination (IP:port), pool, status, IP protocol, profiles, and rules.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: Common).",
        },
    },
)
async def list_virtual_servers(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/virtual", params)
    items = data.get("items", [])
    return [
        {k: v.get(k) for k in (
            "name", "fullPath", "destination", "pool",
            "ipProtocol", "enabled", "disabled",
            "sourceAddressTranslation",
        ) if v.get(k) is not None}
        for v in items
    ]


@tool(
    "get_virtual_server",
    "Get detailed information about a specific virtual server including profiles, policies, rules, persistence, and status.",
    properties={
        "name": {
            "type": "string",
            "description": "Full name of the virtual server (e.g. '/Common/my_vs').",
        },
    },
    required=["name"],
)
async def get_virtual_server(args):
    path = f"/tm/ltm/virtual/{_to_path(args['name'])}"
    data = await _get(path)
    data.pop("selfLink", None)
    data.pop("kind", None)
    data.pop("generation", None)
    # Fetch associated profiles
    try:
        profiles = await _get(f"{path}/profiles")
        data["profiles"] = [
            {"name": p.get("name"), "context": p.get("context")}
            for p in profiles.get("items", [])
        ]
    except Exception:
        pass
    # Fetch associated policies
    try:
        policies = await _get(f"{path}/policies")
        data["policies"] = [
            {"name": p.get("name")} for p in policies.get("items", [])
        ]
    except Exception:
        pass
    return data


@tool(
    "create_virtual_server",
    "Create a new LTM virtual server.",
    properties={
        "name": {
            "type": "string",
            "description": "Name of the virtual server.",
        },
        "destination": {
            "type": "string",
            "description": "Destination IP and port (e.g. '10.10.10.100:80' or '10.10.10.100:443').",
        },
        "pool": {
            "type": "string",
            "description": "Default pool name (e.g. '/Common/my_pool').",
        },
        "ip_protocol": {
            "type": "string",
            "description": "IP protocol: tcp, udp, or sctp (default: tcp).",
        },
        "source_address_translation": {
            "type": "string",
            "description": "Source address translation type: automap, snat, lsn, or none (default: automap).",
        },
        "profiles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of profile names to attach (e.g. ['/Common/http', '/Common/tcp']).",
        },
        "partition": {
            "type": "string",
            "description": "Partition (default: Common).",
        },
        "description": {
            "type": "string",
            "description": "Optional description.",
        },
    },
    required=["name", "destination"],
)
async def create_virtual_server(args):
    partition = args.get("partition", "Common")
    payload: dict[str, Any] = {
        "name": args["name"],
        "partition": partition,
        "destination": f"/{partition}/{args['destination']}",
        "ipProtocol": args.get("ip_protocol", "tcp"),
        "sourceAddressTranslation": {"type": args.get("source_address_translation", "automap")},
    }
    if args.get("pool"):
        payload["pool"] = args["pool"]
    if args.get("profiles"):
        payload["profiles"] = [{"name": p} for p in args["profiles"]]
    if args.get("description"):
        payload["description"] = args["description"]
    data = await _post("/tm/ltm/virtual", payload)
    return {"created": data.get("fullPath", args["name"])}


@tool(
    "modify_virtual_server",
    "Modify an existing LTM virtual server. Only supplied fields are changed.",
    properties={
        "name": {
            "type": "string",
            "description": "Full name of the virtual server (e.g. '/Common/my_vs').",
        },
        "destination": {
            "type": "string",
            "description": "New destination IP:port.",
        },
        "pool": {
            "type": "string",
            "description": "New default pool name.",
        },
        "enabled": {
            "type": "boolean",
            "description": "Set to true to enable, false to disable.",
        },
        "description": {
            "type": "string",
            "description": "New description.",
        },
        "source_address_translation": {
            "type": "string",
            "description": "Source address translation type: automap, snat, lsn, or none.",
        },
    },
    required=["name"],
)
async def modify_virtual_server(args):
    name = args.pop("name")
    payload = {}
    if "destination" in args:
        payload["destination"] = args["destination"]
    if "pool" in args:
        payload["pool"] = args["pool"]
    if "enabled" in args:
        if args["enabled"]:
            payload["enabled"] = True
            payload.pop("disabled", None)
        else:
            payload["disabled"] = True
            payload.pop("enabled", None)
    if "description" in args:
        payload["description"] = args["description"]
    if "source_address_translation" in args:
        payload["sourceAddressTranslation"] = {"type": args["source_address_translation"]}
    data = await _patch(f"/tm/ltm/virtual/{_to_path(name)}", payload)
    return {"modified": data.get("fullPath", name)}


@tool(
    "delete_virtual_server",
    "Delete an LTM virtual server.",
    properties={
        "name": {
            "type": "string",
            "description": "Full name of the virtual server (e.g. '/Common/my_vs').",
        },
    },
    required=["name"],
)
async def delete_virtual_server(args):
    await _delete(f"/tm/ltm/virtual/{_to_path(args['name'])}")
    return {"deleted": args["name"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – POOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_pools",
    "List all LTM pools with their load balancing mode and monitor.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: Common).",
        },
    },
)
async def list_pools(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/pool", params)
    return [
        {k: p.get(k) for k in (
            "name", "fullPath", "loadBalancingMode", "monitor",
            "minActiveMembers", "description",
        ) if p.get(k) is not None}
        for p in data.get("items", [])
    ]


@tool(
    "get_pool",
    "Get detailed information about a pool including its members and their status.",
    properties={
        "name": {
            "type": "string",
            "description": "Full name of the pool (e.g. '/Common/my_pool').",
        },
    },
    required=["name"],
)
async def get_pool(args):
    path = f"/tm/ltm/pool/{_to_path(args['name'])}"
    data = await _get(path)
    data.pop("selfLink", None)
    data.pop("kind", None)
    data.pop("generation", None)
    # Fetch members
    try:
        members_data = await _get(f"{path}/members")
        data["members"] = [
            {k: m.get(k) for k in (
                "name", "fullPath", "address", "state", "session",
                "monitor", "ratio", "priority",
            ) if m.get(k) is not None}
            for m in members_data.get("items", [])
        ]
    except Exception:
        data["members"] = []
    return data


@tool(
    "create_pool",
    "Create a new LTM pool.",
    properties={
        "name": {
            "type": "string",
            "description": "Pool name.",
        },
        "monitor": {
            "type": "string",
            "description": "Health monitor(s) to assign (e.g. '/Common/http' or '/Common/tcp and /Common/http').",
        },
        "load_balancing_mode": {
            "type": "string",
            "description": "Load balancing method: round-robin, least-connections-member, ratio-member, observed-member, predictive-member, etc. (default: round-robin).",
        },
        "members": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of pool members as 'address:port' (e.g. ['10.0.0.1:80', '10.0.0.2:80']).",
        },
        "partition": {
            "type": "string",
            "description": "Partition (default: Common).",
        },
        "description": {
            "type": "string",
            "description": "Optional description.",
        },
    },
    required=["name"],
)
async def create_pool(args):
    payload: dict[str, Any] = {
        "name": args["name"],
        "partition": args.get("partition", "Common"),
    }
    if args.get("monitor"):
        payload["monitor"] = args["monitor"]
    if args.get("load_balancing_mode"):
        payload["loadBalancingMode"] = args["load_balancing_mode"]
    if args.get("members"):
        payload["members"] = [
            {"name": m, "address": m.rsplit(":", 1)[0]}
            for m in args["members"]
        ]
    if args.get("description"):
        payload["description"] = args["description"]
    data = await _post("/tm/ltm/pool", payload)
    return {"created": data.get("fullPath", args["name"])}


@tool(
    "modify_pool",
    "Modify an existing LTM pool. Only supplied fields are changed.",
    properties={
        "name": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
        "monitor": {
            "type": "string",
            "description": "New health monitor(s).",
        },
        "load_balancing_mode": {
            "type": "string",
            "description": "New load balancing method.",
        },
        "description": {
            "type": "string",
            "description": "New description.",
        },
    },
    required=["name"],
)
async def modify_pool(args):
    name = args.pop("name")
    payload = {}
    if "monitor" in args:
        payload["monitor"] = args["monitor"]
    if "load_balancing_mode" in args:
        payload["loadBalancingMode"] = args["load_balancing_mode"]
    if "description" in args:
        payload["description"] = args["description"]
    data = await _patch(f"/tm/ltm/pool/{_to_path(name)}", payload)
    return {"modified": data.get("fullPath", name)}


@tool(
    "delete_pool",
    "Delete an LTM pool.",
    properties={
        "name": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
    },
    required=["name"],
)
async def delete_pool(args):
    await _delete(f"/tm/ltm/pool/{_to_path(args['name'])}")
    return {"deleted": args["name"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – POOL MEMBERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_pool_members",
    "List all members of a specific pool with their state and session status.",
    properties={
        "pool": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
    },
    required=["pool"],
)
async def list_pool_members(args):
    data = await _get(f"/tm/ltm/pool/{_to_path(args['pool'])}/members")
    return [
        {k: m.get(k) for k in (
            "name", "fullPath", "address", "state", "session",
            "monitor", "ratio", "priority", "connectionLimit",
        ) if m.get(k) is not None}
        for m in data.get("items", [])
    ]


@tool(
    "add_pool_member",
    "Add a node as a member to a pool.",
    properties={
        "pool": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
        "member": {
            "type": "string",
            "description": "Member address and port (e.g. '10.0.0.1:80' or '/Common/node1:80').",
        },
        "ratio": {
            "type": "integer",
            "description": "Member weight ratio (default: 1).",
        },
        "priority": {
            "type": "integer",
            "description": "Priority group activation (default: 0).",
        },
        "description": {
            "type": "string",
            "description": "Optional description.",
        },
    },
    required=["pool", "member"],
)
async def add_pool_member(args):
    payload: dict[str, Any] = {"name": args["member"]}
    if ":" in args["member"] and not args["member"].startswith("/"):
        payload["address"] = args["member"].rsplit(":", 1)[0]
    if args.get("ratio"):
        payload["ratio"] = args["ratio"]
    if args.get("priority"):
        payload["priorityGroup"] = args["priority"]
    if args.get("description"):
        payload["description"] = args["description"]
    data = await _post(
        f"/tm/ltm/pool/{_to_path(args['pool'])}/members", payload
    )
    return {"added": data.get("fullPath", args["member"])}


@tool(
    "remove_pool_member",
    "Remove a member from a pool.",
    properties={
        "pool": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
        "member": {
            "type": "string",
            "description": "Member name (e.g. '/Common/10.0.0.1:80').",
        },
    },
    required=["pool", "member"],
)
async def remove_pool_member(args):
    pool_path = _to_path(args["pool"])
    member_path = _to_path(args["member"])
    await _delete(f"/tm/ltm/pool/{pool_path}/members/{member_path}")
    return {"removed": args["member"], "from_pool": args["pool"]}


@tool(
    "set_pool_member_state",
    "Enable, disable (mark offline gracefully), or force-offline a pool member.",
    properties={
        "pool": {
            "type": "string",
            "description": "Full pool name (e.g. '/Common/my_pool').",
        },
        "member": {
            "type": "string",
            "description": "Member name (e.g. '/Common/10.0.0.1:80').",
        },
        "state": {
            "type": "string",
            "description": "Desired state: 'enabled', 'disabled' (graceful), or 'forced-offline'.",
            "enum": ["enabled", "disabled", "forced-offline"],
        },
    },
    required=["pool", "member", "state"],
)
async def set_pool_member_state(args):
    pool_path = _to_path(args["pool"])
    member_path = _to_path(args["member"])
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    payload = state_map[args["state"]]
    await _patch(f"/tm/ltm/pool/{pool_path}/members/{member_path}", payload)
    return {"member": args["member"], "state": args["state"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – NODES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_nodes",
    "List all LTM nodes with their addresses and status.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: Common).",
        },
    },
)
async def list_nodes(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/node", params)
    return [
        {k: n.get(k) for k in (
            "name", "fullPath", "address", "state", "session",
            "monitor", "description",
        ) if n.get(k) is not None}
        for n in data.get("items", [])
    ]


@tool(
    "get_node",
    "Get detailed information about a specific node.",
    properties={
        "name": {
            "type": "string",
            "description": "Full node name (e.g. '/Common/10.0.0.1' or '/Common/web1').",
        },
    },
    required=["name"],
)
async def get_node(args):
    data = await _get(f"/tm/ltm/node/{_to_path(args['name'])}")
    for key in ("selfLink", "kind", "generation"):
        data.pop(key, None)
    return data


@tool(
    "create_node",
    "Create a new LTM node.",
    properties={
        "name": {
            "type": "string",
            "description": "Node name.",
        },
        "address": {
            "type": "string",
            "description": "IP address of the node.",
        },
        "monitor": {
            "type": "string",
            "description": "Health monitor to assign (e.g. '/Common/icmp').",
        },
        "partition": {
            "type": "string",
            "description": "Partition (default: Common).",
        },
        "description": {
            "type": "string",
            "description": "Optional description.",
        },
    },
    required=["name", "address"],
)
async def create_node(args):
    payload: dict[str, Any] = {
        "name": args["name"],
        "address": args["address"],
        "partition": args.get("partition", "Common"),
    }
    if args.get("monitor"):
        payload["monitor"] = args["monitor"]
    if args.get("description"):
        payload["description"] = args["description"]
    data = await _post("/tm/ltm/node", payload)
    return {"created": data.get("fullPath", args["name"])}


@tool(
    "delete_node",
    "Delete an LTM node.",
    properties={
        "name": {
            "type": "string",
            "description": "Full node name (e.g. '/Common/web1').",
        },
    },
    required=["name"],
)
async def delete_node(args):
    await _delete(f"/tm/ltm/node/{_to_path(args['name'])}")
    return {"deleted": args["name"]}


@tool(
    "set_node_state",
    "Enable, disable, or force-offline a node.",
    properties={
        "name": {
            "type": "string",
            "description": "Full node name (e.g. '/Common/web1').",
        },
        "state": {
            "type": "string",
            "description": "Desired state: 'enabled', 'disabled', or 'forced-offline'.",
            "enum": ["enabled", "disabled", "forced-offline"],
        },
    },
    required=["name", "state"],
)
async def set_node_state(args):
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    await _patch(f"/tm/ltm/node/{_to_path(args['name'])}", state_map[args["state"]])
    return {"node": args["name"], "state": args["state"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – MONITORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_monitors",
    "List health monitors of a given type.",
    properties={
        "type": {
            "type": "string",
            "description": "Monitor type: http, https, tcp, udp, icmp, gateway-icmp, tcp-half-open, etc.",
        },
    },
    required=["type"],
)
async def list_monitors(args):
    data = await _get(f"/tm/ltm/monitor/{args['type']}")
    return [
        {k: m.get(k) for k in (
            "name", "fullPath", "interval", "timeout",
            "send", "recv", "destination",
        ) if m.get(k) is not None}
        for m in data.get("items", [])
    ]


@tool(
    "get_monitor",
    "Get detailed configuration of a specific health monitor.",
    properties={
        "type": {
            "type": "string",
            "description": "Monitor type (e.g. 'http', 'tcp').",
        },
        "name": {
            "type": "string",
            "description": "Full monitor name (e.g. '/Common/http').",
        },
    },
    required=["type", "name"],
)
async def get_monitor(args):
    data = await _get(f"/tm/ltm/monitor/{args['type']}/{_to_path(args['name'])}")
    for key in ("selfLink", "kind", "generation"):
        data.pop(key, None)
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – iRULES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_irules",
    "List all iRules.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: all).",
        },
    },
)
async def list_irules(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/rule", params)
    return [
        {"name": r.get("name"), "fullPath": r.get("fullPath")}
        for r in data.get("items", [])
    ]


@tool(
    "get_irule",
    "Get the content/code of a specific iRule.",
    properties={
        "name": {
            "type": "string",
            "description": "Full iRule name (e.g. '/Common/my_irule').",
        },
    },
    required=["name"],
)
async def get_irule(args):
    data = await _get(f"/tm/ltm/rule/{_to_path(args['name'])}")
    return {
        "name": data.get("fullPath"),
        "apiAnonymous": data.get("apiAnonymous", ""),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – PROFILES & PERSISTENCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_profiles",
    "List profiles of a specific type.",
    properties={
        "type": {
            "type": "string",
            "description": "Profile type: tcp, udp, http, client-ssl, server-ssl, fasthttp, fastl4, one-connect, http-compression, web-acceleration, etc.",
        },
    },
    required=["type"],
)
async def list_profiles(args):
    data = await _get(f"/tm/ltm/profile/{args['type']}")
    return [
        {"name": p.get("name"), "fullPath": p.get("fullPath"), "defaultsFrom": p.get("defaultsFrom")}
        for p in data.get("items", [])
    ]


@tool(
    "list_persistence_profiles",
    "List persistence profiles of a specific type.",
    properties={
        "type": {
            "type": "string",
            "description": "Persistence type: cookie, dest-addr, source-addr, ssl, universal, hash, host, msrdp, sip.",
        },
    },
    required=["type"],
)
async def list_persistence_profiles(args):
    data = await _get(f"/tm/ltm/persistence/{args['type']}")
    return [
        {"name": p.get("name"), "fullPath": p.get("fullPath"), "defaultsFrom": p.get("defaultsFrom")}
        for p in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – DATA GROUPS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_data_groups",
    "List internal data groups.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: all).",
        },
    },
)
async def list_data_groups(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/data-group/internal", params)
    return [
        {"name": d.get("name"), "fullPath": d.get("fullPath"), "type": d.get("type")}
        for d in data.get("items", [])
    ]


@tool(
    "get_data_group",
    "Get the records of an internal data group.",
    properties={
        "name": {
            "type": "string",
            "description": "Full data group name (e.g. '/Common/my_dg').",
        },
    },
    required=["name"],
)
async def get_data_group(args):
    data = await _get(f"/tm/ltm/data-group/internal/{_to_path(args['name'])}")
    return {
        "name": data.get("fullPath"),
        "type": data.get("type"),
        "records": data.get("records", []),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – POLICIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_policies",
    "List all LTM policies.",
    properties={
        "partition": {
            "type": "string",
            "description": "Partition name (default: all).",
        },
    },
)
async def list_policies(args):
    params = {}
    partition = args.get("partition")
    if partition:
        params["$filter"] = f"partition eq {partition}"
    data = await _get("/tm/ltm/policy", params)
    return [
        {"name": p.get("name"), "fullPath": p.get("fullPath"), "strategy": p.get("strategy"), "status": p.get("status")}
        for p in data.get("items", [])
    ]


@tool(
    "get_policy",
    "Get detailed configuration of an LTM policy including its rules.",
    properties={
        "name": {
            "type": "string",
            "description": "Full policy name (e.g. '/Common/my_policy').",
        },
    },
    required=["name"],
)
async def get_policy(args):
    data = await _get(f"/tm/ltm/policy/{_to_path(args['name'])}")
    for key in ("selfLink", "kind", "generation"):
        data.pop(key, None)
    try:
        rules = await _get(f"/tm/ltm/policy/{_to_path(args['name'])}/rules")
        data["rules"] = _strip_meta(rules.get("items", []))
    except Exception:
        pass
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – SNAT POOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_snat_pools",
    "List all SNAT pools.",
)
async def list_snat_pools(args):
    data = await _get("/tm/ltm/snatpool")
    return [
        {"name": s.get("name"), "fullPath": s.get("fullPath"), "members": s.get("members", [])}
        for s in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NETWORK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_vlans",
    "List all VLANs with their tag and interfaces.",
)
async def list_vlans(args):
    data = await _get("/tm/net/vlan")
    return [
        {k: v.get(k) for k in (
            "name", "fullPath", "tag", "mtu", "autoLasthop",
            "description",
        ) if v.get(k) is not None}
        for v in data.get("items", [])
    ]


@tool(
    "get_vlan",
    "Get detailed VLAN configuration including interfaces.",
    properties={
        "name": {
            "type": "string",
            "description": "Full VLAN name (e.g. '/Common/external').",
        },
    },
    required=["name"],
)
async def get_vlan(args):
    path = f"/tm/net/vlan/{_to_path(args['name'])}"
    data = await _get(path)
    for key in ("selfLink", "kind", "generation"):
        data.pop(key, None)
    try:
        ifaces = await _get(f"{path}/interfaces")
        data["interfaces"] = [
            {"name": i.get("name"), "tagged": i.get("tagged", False)}
            for i in ifaces.get("items", [])
        ]
    except Exception:
        pass
    return data


@tool(
    "create_vlan",
    "Create a new VLAN.",
    properties={
        "name": {
            "type": "string",
            "description": "VLAN name.",
        },
        "tag": {
            "type": "integer",
            "description": "VLAN tag ID (1-4094).",
        },
        "interfaces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Interface name (e.g. '1.1')"},
                    "tagged": {"type": "boolean", "description": "Whether the interface is tagged (default: true)"},
                },
            },
            "description": "Interfaces to assign to the VLAN.",
        },
        "partition": {
            "type": "string",
            "description": "Partition (default: Common).",
        },
    },
    required=["name"],
)
async def create_vlan(args):
    payload: dict[str, Any] = {
        "name": args["name"],
        "partition": args.get("partition", "Common"),
    }
    if args.get("tag"):
        payload["tag"] = args["tag"]
    if args.get("interfaces"):
        payload["interfaces"] = args["interfaces"]
    data = await _post("/tm/net/vlan", payload)
    return {"created": data.get("fullPath", args["name"])}


@tool(
    "delete_vlan",
    "Delete a VLAN.",
    properties={
        "name": {
            "type": "string",
            "description": "Full VLAN name (e.g. '/Common/my_vlan').",
        },
    },
    required=["name"],
)
async def delete_vlan(args):
    await _delete(f"/tm/net/vlan/{_to_path(args['name'])}")
    return {"deleted": args["name"]}


@tool(
    "list_self_ips",
    "List all self IP addresses.",
)
async def list_self_ips(args):
    data = await _get("/tm/net/self")
    return [
        {k: s.get(k) for k in (
            "name", "fullPath", "address", "vlan",
            "trafficGroup", "floating", "allowService",
        ) if s.get(k) is not None}
        for s in data.get("items", [])
    ]


@tool(
    "create_self_ip",
    "Create a new self IP address.",
    properties={
        "name": {
            "type": "string",
            "description": "Self IP name.",
        },
        "address": {
            "type": "string",
            "description": "IP address with CIDR (e.g. '10.10.10.1/24').",
        },
        "vlan": {
            "type": "string",
            "description": "VLAN to assign (e.g. '/Common/external').",
        },
        "traffic_group": {
            "type": "string",
            "description": "Traffic group (e.g. 'traffic-group-local-only').",
        },
        "allow_service": {
            "type": "string",
            "description": "Allowed services: 'all', 'default', 'none', or specific like 'tcp:443'.",
        },
        "partition": {
            "type": "string",
            "description": "Partition (default: Common).",
        },
    },
    required=["name", "address", "vlan"],
)
async def create_self_ip(args):
    payload: dict[str, Any] = {
        "name": args["name"],
        "address": args["address"],
        "vlan": args["vlan"],
        "partition": args.get("partition", "Common"),
    }
    if args.get("traffic_group"):
        payload["trafficGroup"] = args["traffic_group"]
    if args.get("allow_service"):
        payload["allowService"] = [args["allow_service"]]
    data = await _post("/tm/net/self", payload)
    return {"created": data.get("fullPath", args["name"])}


@tool(
    "delete_self_ip",
    "Delete a self IP address.",
    properties={
        "name": {
            "type": "string",
            "description": "Full self IP name (e.g. '/Common/my_self_ip').",
        },
    },
    required=["name"],
)
async def delete_self_ip(args):
    await _delete(f"/tm/net/self/{_to_path(args['name'])}")
    return {"deleted": args["name"]}


@tool(
    "list_routes",
    "List all static routes.",
)
async def list_routes(args):
    data = await _get("/tm/net/route")
    return [
        {k: r.get(k) for k in (
            "name", "fullPath", "network", "gw", "pool",
            "blackhole", "mtu",
        ) if r.get(k) is not None}
        for r in data.get("items", [])
    ]


@tool(
    "list_interfaces",
    "List all network interfaces and their status.",
)
async def list_interfaces(args):
    data = await _get("/tm/net/interface")
    return [
        {k: i.get(k) for k in (
            "name", "fullPath", "macAddress", "mediaActive",
            "mediaMax", "mtu", "enabled", "disabled",
        ) if i.get(k) is not None}
        for i in data.get("items", [])
    ]


@tool(
    "list_trunks",
    "List all trunk groups (link aggregation).",
)
async def list_trunks(args):
    data = await _get("/tm/net/trunk")
    return [
        {k: t.get(k) for k in (
            "name", "fullPath", "interfaces", "lacp",
            "lacpMode", "bandwidth", "media",
        ) if t.get(k) is not None}
        for t in data.get("items", [])
    ]


@tool(
    "list_route_domains",
    "List all route domains.",
)
async def list_route_domains(args):
    data = await _get("/tm/net/route-domain")
    return [
        {k: rd.get(k) for k in (
            "name", "fullPath", "id", "vlans",
            "strict", "parent",
        ) if rd.get(k) is not None}
        for rd in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GTM / DNS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_gtm_wideips",
    "List GTM wide IPs of a specified record type.",
    properties={
        "type": {
            "type": "string",
            "description": "DNS record type: a, aaaa, cname, mx, naptr, srv.",
        },
    },
    required=["type"],
)
async def list_gtm_wideips(args):
    data = await _get(f"/tm/gtm/wideip/{args['type']}")
    return [
        {k: w.get(k) for k in (
            "name", "fullPath", "poolLbMode", "enabled",
            "disabled", "lastResortPool", "description",
        ) if w.get(k) is not None}
        for w in data.get("items", [])
    ]


@tool(
    "get_gtm_wideip",
    "Get detailed configuration of a GTM wide IP.",
    properties={
        "type": {
            "type": "string",
            "description": "DNS record type: a, aaaa, cname, mx, naptr, srv.",
        },
        "name": {
            "type": "string",
            "description": "Full wide IP name (e.g. '/Common/www.example.com').",
        },
    },
    required=["type", "name"],
)
async def get_gtm_wideip(args):
    data = await _get(f"/tm/gtm/wideip/{args['type']}/{_to_path(args['name'])}")
    for key in ("selfLink", "kind", "generation"):
        data.pop(key, None)
    return data


@tool(
    "list_gtm_pools",
    "List GTM pools of a specified record type.",
    properties={
        "type": {
            "type": "string",
            "description": "DNS record type: a, aaaa, cname, mx, naptr, srv.",
        },
    },
    required=["type"],
)
async def list_gtm_pools(args):
    data = await _get(f"/tm/gtm/pool/{args['type']}")
    return [
        {k: p.get(k) for k in (
            "name", "fullPath", "loadBalancingMode",
            "monitor", "enabled", "disabled",
        ) if p.get(k) is not None}
        for p in data.get("items", [])
    ]


@tool(
    "list_gtm_datacenters",
    "List all GTM data centers.",
)
async def list_gtm_datacenters(args):
    data = await _get("/tm/gtm/datacenter")
    return [
        {k: dc.get(k) for k in (
            "name", "fullPath", "contact", "location",
            "enabled", "disabled", "description",
        ) if dc.get(k) is not None}
        for dc in data.get("items", [])
    ]


@tool(
    "list_gtm_servers",
    "List all GTM servers.",
)
async def list_gtm_servers(args):
    data = await _get("/tm/gtm/server")
    return [
        {k: s.get(k) for k in (
            "name", "fullPath", "datacenter", "product",
            "enabled", "disabled", "monitor",
        ) if s.get(k) is not None}
        for s in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CM – DEVICE MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_device_groups",
    "List all device groups for HA clustering.",
)
async def list_device_groups(args):
    data = await _get("/tm/cm/device-group")
    return [
        {k: dg.get(k) for k in (
            "name", "fullPath", "type", "autoSync",
            "networkFailover", "description",
        ) if dg.get(k) is not None}
        for dg in data.get("items", [])
    ]


@tool(
    "list_traffic_groups",
    "List all traffic groups for HA failover.",
)
async def list_traffic_groups(args):
    data = await _get("/tm/cm/traffic-group")
    return [
        {k: tg.get(k) for k in (
            "name", "fullPath", "autoFailbackEnabled",
            "failoverMethod", "haOrder",
        ) if tg.get(k) is not None}
        for tg in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  UTILITY – BASH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "run_bash_command",
    "Execute a bash command on the BIG-IP device via the REST API. Use for tmsh commands or shell commands.",
    properties={
        "command": {
            "type": "string",
            "description": "The bash command to execute (e.g. 'tmsh list ltm virtual' or 'cat /var/log/ltm').",
        },
    },
    required=["command"],
)
async def run_bash_command(args):
    payload = {
        "command": "run",
        "utilCmdArgs": f"-c '{args['command']}'",
    }
    data = await _post("/tm/util/bash", payload)
    return {"output": data.get("commandResult", "")}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – VIRTUAL ADDRESS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_virtual_addresses",
    "List all LTM virtual addresses.",
)
async def list_virtual_addresses(args):
    data = await _get("/tm/ltm/virtual-address")
    return [
        {k: va.get(k) for k in (
            "name", "fullPath", "address", "arp",
            "enabled", "floating", "trafficGroup",
        ) if va.get(k) is not None}
        for va in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LTM – SSL CERTIFICATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@tool(
    "list_ssl_certificates",
    "List all SSL certificates on the BIG-IP.",
)
async def list_ssl_certificates(args):
    data = await _get("/tm/sys/file/ssl-cert")
    return [
        {k: c.get(k) for k in (
            "name", "fullPath", "commonName", "subjectAlternativeName",
            "expirationDate", "issuer", "keyType",
        ) if c.get(k) is not None}
        for c in data.get("items", [])
    ]


@tool(
    "list_ssl_keys",
    "List all SSL keys on the BIG-IP.",
)
async def list_ssl_keys(args):
    data = await _get("/tm/sys/file/ssl-key")
    return [
        {k: k_item.get(k) for k in (
            "name", "fullPath", "keyType", "keySize", "securityType",
        ) if k_item.get(k) is not None}
        for k_item in data.get("items", [])
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MCP PROTOCOL HANDLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def handle_mcp_message(message: dict) -> dict | None:
    """Process a JSON-RPC 2.0 MCP message and return a response."""
    method = message.get("method", "")
    msg_id = message.get("id")
    params = message.get("params", {})

    # ── Initialize ────────────────────────────────────────────────────
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }

    # ── Initialized (notification, no response) ──────────────────────
    if method == "notifications/initialized":
        return None

    # ── List tools ────────────────────────────────────────────────────
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    # ── Call tool ─────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}",
                },
            }
        try:
            result = await handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2, default=str),
                        }
                    ],
                    "isError": False,
                },
            }
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            try:
                body = json.dumps(exc.response.json(), indent=2)
            except Exception:
                pass
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"BIG-IP API error {exc.response.status_code}:\n{body}",
                        }
                    ],
                    "isError": True,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": f"Error: {exc}"}
                    ],
                    "isError": True,
                },
            }

    # ── Ping ──────────────────────────────────────────────────────────
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FASTAPI APP + TRANSPORTS (SSE & WebSocket)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(title="BIG-IP MCP Server")
sse_sessions: dict[str, asyncio.Queue] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "server": SERVER_INFO["name"], "tools": len(TOOLS)}


# ── SSE Transport ─────────────────────────────────────────────────────
@app.get("/sse")
async def sse_endpoint(request: Request):
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    sse_sessions[session_id] = queue
    log.info("SSE session started: %s", session_id)

    async def event_generator():
        yield {
            "event": "endpoint",
            "data": f"/messages?session_id={session_id}",
        }
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": "message", "data": json.dumps(msg)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            sse_sessions.pop(session_id, None)
            log.info("SSE session ended: %s", session_id)

    return EventSourceResponse(event_generator())


@app.post("/messages")
async def sse_messages(request: Request, session_id: str = Query(...)):
    queue = sse_sessions.get(session_id)
    if not queue:
        return JSONResponse({"error": "Invalid session"}, status_code=400)
    body = await request.json()
    log.info("SSE message [%s]: method=%s", session_id, body.get("method"))
    response = await handle_mcp_message(body)
    if response:
        await queue.put(response)
    return JSONResponse({"ok": True})


# ── WebSocket Transport ──────────────────────────────────────────────
@app.websocket("/")
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("WebSocket connected: %s", ws.client)
    try:
        while True:
            raw = await ws.receive_text()
            message = json.loads(raw)
            log.info("WS message: method=%s", message.get("method"))
            response = await handle_mcp_message(message)
            if response:
                await ws.send_text(json.dumps(response))
    except WebSocketDisconnect:
        log.info("WebSocket disconnected: %s", ws.client)
    except Exception as exc:
        log.error("WebSocket error: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import uvicorn

    log.info("Starting BIG-IP MCP Server on port %d with %d tools", MCP_PORT, len(TOOLS))
    log.info("BIG-IP target: %s:%s", BIGIP_HOST, BIGIP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
