# Intel XPU SmartTune Backend API Guide

This document provides a comprehensive reference for the Intel XPU SmartTune backend API endpoints.

## Base Information

| Item | Value |
|------|-------|
| Base URL | `https://localhost:9001` |
| Protocol | HTTPS (requires `b_server.crt` / `b_server.key`) |
| Response Format | JSON (default) or Server-Sent Events (SSE) for streaming endpoints |
| Authentication | Token-based (via `/auth/login`) |
| Framework | Flask (WSGI) |
| CORS | Enabled (`Access-Control-Allow-Origin: *`) |

## Unified Response Format

All endpoints return a standardized JSON structure:

```json
{
  "retcode": 0,
  "retmsg": "success",
  "data": { ... }
}
```

### Return Codes (RetCode)

| Code | Name | Description |
|------|------|-------------|
| 0 | SUCCESS | Request completed successfully |
| 10 | NOT_EFFECTIVE | Operation had no effect |
| 100 | EXCEPTION_ERROR | Internal server error / unhandled exception |
| 101 | ARGUMENT_ERROR | Missing or invalid request parameters |
| 102 | DATA_ERROR | Data validation error |
| 103 | OPERATING_ERROR | Operation failed (e.g., resource not found for action) |
| 105 | CONNECTION_ERROR | External connection failure |
| 106 | RUNNING | Process already running |
| 108 | PERMISSION_ERROR | Insufficient permissions |
| 109 | AUTHENTICATION_ERROR | Authentication failed |
| 401 | UNAUTHORIZED | Unauthorized access |
| 404 | NOT_EXISTING | Requested resource does not exist |
| 409 | CONFLICT | Optimistic concurrency conflict |
| 500 | SERVER_ERROR | Internal server error |

---

## API Overview Table

### Part 1: Authentication & Application Management

| Category | Endpoint | Method | Description | Key Features |
|----------|----------|--------|-------------|--------------|
| Auth | `/auth/login` | POST | User authentication | SHA256 token validation |
| App | `/app/get_apps` | GET, POST | List all apps | Optional DB sync |
| App | `/app/set_priority` | POST | Set app priority | OOM score auto-adjustment |
| App | `/app/get_priority_data` | POST | Get priority info | Query by app_id or name |
| App | `/app/set_to_control` | POST | Enable app control | Register with BPF monitor |
| App | `/app/remove_from_control` | POST | Remove from control | Unregister, restore OOM |
| App | `/app/get_controlled_app` | POST | List controlled apps | Full metadata, status |
| App | `/app/check_running_apps` | POST | Scan running processes | Detect pre-existing apps |
| App | `/app/get_pending_app` | POST | List pending apps | Sorted by priority DESC |
| App | `/app/set_oom_score` | POST | Set OOM score | Protect from OOM killer |
| App | `/app/cancel_relaunch` | POST | Cancel app relaunch | By app_id |
| App | `/app/resource_limit` | POST | Set resource limit | cgroup-based, overridable |
| App | `/app/resource_limit_profile` | POST | Get limit profile | Defaults + bounds for UI |
| App | `/app/resource_restore` | POST | Restore resources | Remove limits by app_id |
| App | `/app/events` | GET | SSE event stream | Real-time status push |

### Part 2: System Monitor

| Category | Endpoint | Method | Description | Key Features |
|----------|----------|--------|-------------|--------------|
| Monitor | `/monitor/app_resource_stats` | GET | App CPU/memory usage | Background-cached, top-N |
| Monitor | `/monitor/app_disk_io_stats` | GET | App disk I/O usage | Throughput + IOPS |
| Monitor | `/monitor/processes` | GET | All processes | Like `top`, sorted by CPU |
| Monitor | `/monitor/static_info` | GET | System hardware info | BIOS/OS/CPU/GPU/NPU |
| Monitor | `/monitor/dynamic_info` | GET | Live system metrics (CPU, memory, IO, GPU, NPU) | Auto-refresh cached (2s interval) |
| Monitor | `/monitor/history` | GET | Snapshot history (dynamic_info) | Time-range, type filter, limit support |
| Monitor | `/monitor/history/retention` | GET | Get retention config | Current period + bounds |
| Monitor | `/monitor/history/retention` | POST | Set retention period | Optimistic concurrency |
| Config | `/monitor/config/weights_top` | GET | Get ranking weights | CPU/memory/GPU weights |
| Config | `/monitor/config/weights_top` | POST | Update ranking weights | Optimistic concurrency |
| Config | `/monitor/config/passive_control` | GET | Get passive control state | Enable/disable flag |
| Config | `/monitor/config/passive_control` | POST | Toggle passive control | Optimistic concurrency |

---

## Part 1 — Detailed API Specifications

### Authentication

#### POST /auth/login

**Purpose:** Validate user-provided token against the stored hash for authentication.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | pwd | Yes | string | User token for authentication |

**Request Example:**
```json
{
  "pwd": "your-secret-token"
}
```

**Response (Success):**
```json
{
  "retcode": 0,
  "retmsg": "Authentication successful",
  "data": {
    "authenticated": true
  }
}
```

**Response (Invalid Token):**
```json
{
  "retcode": 0,
  "retmsg": "Invalid token",
  "data": {
    "authenticated": false
  }
}
```

---

### Application Management

#### GET/POST /app/get_apps

**Purpose:** Retrieve all system application entries and optionally sync them to the database.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | store | No | boolean | If `true`, sync discovered apps to DB (default: `false`) |

**Request Example:**
```json
{
  "store": true
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved app list",
  "data": [
    {
      "app_id": "com.example.app",
      "name": "Example App",
      "commandline": "/usr/bin/example --flag"
    }
  ]
}
```

---

#### POST /app/set_priority

**Purpose:** Set the priority of an application and update the database. Also adjusts OOM score.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |
| Body | priority | Yes | int | Priority level to set |

**Request Example:**
```json
{
  "app_id": "com.example.app",
  "priority": 5
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Priority updated successfully",
  "data": {}
}
```

---

#### POST /app/get_priority_data

**Purpose:** Retrieve the priority settings for an app by app_id or name.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | No* | string | Application identifier |
| Body | app_name | No* | string | Application name |

*At least one of `app_id` or `app_name` must be provided.

**Request Example:**
```json
{
  "app_id": "com.example.app"
}
```

Or query by name:
```json
{
  "app_name": "example"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved priority data",
  "data": {
    "id": 1,
    "app_id": "com.example.app",
    "name": "Example App",
    "priority": 5,
    "cgroup": "/sys/fs/cgroup/example",
    "remark": "Critical service",
    "cmdline": "/usr/bin/example --flag",
    "up_time": "2026-06-17T10:30:00",
    "status": "running"
  }
}
```

---

#### POST /app/set_to_control

**Purpose:** Enable or disable control for an application and register it with the BPF monitor.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |
| Body | app_name | Yes | string | Application name |
| Body | controlled | No | boolean | Enable/disable control (default: `true`) |
| Body | cgroup | No | string | cgroup path |
| Body | priority | No | int | Priority level (default: `0`) |
| Body | remark | No | string | Remark/description |
| Body | cmdline | No | string | Command line |

**Request Example:**
```json
{
  "app_id": "com.example.app",
  "app_name": "example",
  "controlled": true,
  "priority": 3,
  "cgroup": "",
  "remark": "AI inference workload",
  "cmdline": "/usr/bin/example --mode=inference"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "App control enabled and added to monitor",
  "data": {
    "app_name": "example",
    "controlled": true
  }
}
```

---

#### POST /app/remove_from_control

**Purpose:** Remove an application from the control list and restore its OOM score.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | No* | string | Application identifier |
| Body | app_name | No* | string | Application name |

*At least one must be provided.

**Request Example:**
```json
{
  "app_id": "com.example.app",
  "app_name": "example"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "App removed from control successfully",
  "data": {
    "app_id": "com.example.app",
    "app_name": "example",
    "controlled": false
  }
}
```

---

#### POST /app/get_controlled_app

**Purpose:** Return all controlled applications along with their current metadata.

**Request:** Empty JSON body or no body.

**Request Example:**
```json
{}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Found 3 controlled apps",
  "data": [
    {
      "app_id": "com.example.app",
      "app_name": "Example App",
      "controlled": true,
      "priority": 5,
      "oom_score": -500,
      "cmdline": "/usr/bin/example",
      "cgroup": "",
      "process_names": ["example", "example-worker"],
      "remark": "Critical service",
      "status": "running"
    }
  ]
}
```

---

#### POST /app/check_running_apps

**Purpose:** Scan currently running processes to find managed apps that started before the balancer. Called once when the UI balancer tab is first opened.

**Request:** Empty JSON body or no body.

**Request Example:**
```json
{}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Startup scan complete, detected 2 pre-existing monitored app(s)",
  "data": [
    {
      "app_id": "com.example.app",
      "app_name": "example",
      "status": "running"
    }
  ]
}
```

---

#### POST /app/get_pending_app

**Purpose:** Return all applications currently in pending state, ordered by priority (descending).

**Request:** Empty JSON body or no body.

**Request Example:**
```json
{}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Found 2 pending apps (sorted by priority DESC)",
  "data": [
    {
      "app_id": "com.example.app",
      "app_name": "Example App",
      "controlled": true,
      "priority": 5,
      "oom_score": -500,
      "priority_value": 50,
      "cgroup": "",
      "remark": "",
      "status": "pending"
    }
  ]
}
```

---

#### POST /app/set_oom_score

**Purpose:** Set the OOM score for an application to protect it from the Linux OOM killer.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |

**Request Example:**
```json
{
  "app_id": "com.example.app"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "App OOM score set successfully",
  "data": {}
}
```

---

#### POST /app/cancel_relaunch

**Purpose:** Cancel relaunch for a specific app by app_id. Updates status to "stopped".

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |

**Request Example:**
```json
{
  "app_id": "com.example.app"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully found and canceled relaunch",
  "data": {
    "app_id": "com.example.app"
  }
}
```

---

#### POST /app/resource_limit

**Purpose:** Set resource limit (cgroup-based) for a specific app.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |
| Body | app_name | Yes | string | Application name |
| Body | priority | Yes | string | Priority level |
| Body | limit_overrides | No | object | Custom limit overrides (key-value) |

**Request Example:**
```json
{
  "app_id": "com.example.app",
  "app_name": "example",
  "priority": "3",
  "limit_overrides": {
    "cpu_quota": 50,
    "memory_max_mb": 2048
  }
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully found and set resource limit",
  "data": {}
}
```

---

#### POST /app/resource_limit_profile

**Purpose:** Get the editable resource-limit profile (defaults + bounds) for the UI.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |
| Body | app_name | Yes | string | Application name |
| Body | priority | No | string | Priority level |

**Request Example:**
```json
{
  "app_id": "com.example.app",
  "app_name": "example",
  "priority": "3"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully fetched resource limit profile",
  "data": {
    "cpu_quota": { "default": 50, "min": 10, "max": 100 },
    "memory_max_mb": { "default": 2048, "min": 256, "max": 16384 }
  }
}
```

---

#### POST /app/resource_restore

**Purpose:** Restore (remove) resource limits for a specific app by app_id.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | app_id | Yes | string | Application identifier |

**Request Example:**
```json
{
  "app_id": "com.example.app"
}
```

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully found and restored resource",
  "data": {}
}
```

---

#### GET /app/events

**Purpose:** Server-Sent Events (SSE) stream for real-time app status changes.

**Request:** No body required. Connect via HTTP GET.

**Response Format:** `text/event-stream`

**Response Headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
```

**Event Stream:**
```
data: {"type": "connected"}

data: {"app_id": "com.example.app", "app_name": "example", "status": "running", "purpose": "app"}

: heartbeat
```

**Notes:**
- Initial connection event is sent immediately
- Heartbeat comments (`: heartbeat`) sent every 30 seconds when idle
- Events are JSON-encoded app status updates
- Connection remains open until client disconnects

---

## Part 2 — Monitor API Specifications

### Resource Statistics

#### GET /monitor/app_resource_stats

**Purpose:** Return per-application CPU/memory/GPU resource usage for the dashboard. Background-cached with auto-refresh every 2 seconds.

**Query Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| n | No | int | 10 | Number of top apps to return |

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved app resource stats",
  "data": {
    "apps": [
      {
        "app_id": "com.example.app",
        "app_name": "Example App",
        "pid": 12345,
        "process_name": "example",
        "cmdline": "/usr/bin/example --flag",
        "cpu_usage": 0.35,
        "memory_mb": 1024.5,
        "io_read_rate": 12.3,
        "io_write_rate": 5.6,
        "score": 85.2,
        "gpu_util": 45.0,
        "gpu_mem_mb": 2048.0
      }
    ]
  }
}
```

**Field Details:**
| Field | Type | Description |
|-------|------|-------------|
| cpu_usage | float | Fraction of total CPU capacity (0-1) |
| memory_mb | float | Resident memory in MB |
| io_read_rate | float | Disk read rate in MB/s |
| io_write_rate | float | Disk write rate in MB/s |
| score | float | Combined ranking score |
| gpu_util | float | Peak GPU engine utilization % (0-100) |
| gpu_mem_mb | float | GPU memory used in MB |

---

#### GET /monitor/app_disk_io_stats

**Purpose:** Return per-application disk I/O usage stats. Background-cached with auto-refresh.

**Query Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| n | No | int | 10 | Number of top apps to return |

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved app disk I/O stats",
  "data": {
    "apps": [
      {
        "pid": 12345,
        "name": "example",
        "app_name": "Example App",
        "cmdline": "/usr/bin/example",
        "io_read_rate": 50.2,
        "io_write_rate": 30.1,
        "io_read_iops": 1200.0,
        "io_write_iops": 800.0,
        "score": 72.5
      }
    ]
  }
}
```

**Field Details:**
| Field | Type | Description |
|-------|------|-------------|
| io_read_rate | float | Read throughput in MB/s |
| io_write_rate | float | Write throughput in MB/s |
| io_read_iops | float | Read operations per second |
| io_write_iops | float | Write operations per second |
| score | float | Combined I/O ranking score |

---

#### GET /monitor/processes

**Purpose:** Return a list of all running processes sorted by CPU usage, similar to `top`.

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved process list",
  "data": {
    "count": 256,
    "processes": [
      {
        "pid": 12345,
        "name": "example",
        "username": "root",
        "cpu_percent": 45.2,
        "memory_percent": 8.5,
        "mem_rss_kb": 524288,
        "status": "running",
        "cmdline": "/usr/bin/example --mode=inference"
      }
    ]
  }
}
```

---

### System Information

#### GET /monitor/static_info

**Purpose:** Return static system configuration info (hardware, OS, drivers).

**Query Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| force_refresh | No | string | false | Force re-collection (`1`/`true`/`yes`) |

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved static system info",
  "data": {
    "bios": { "vendor": "...", "version": "...", "release_date": "..." },
    "os": { "name": "...", "version": "...", "kernel": "..." },
    "driver": { "gpu": "...", "version": "..." },
    "cpu": { "model": "...", "cores": 16, "threads": 32 },
    "memory": { "total_gb": 64, "type": "DDR5" },
    "io": { "disks": [...] },
    "gpu": { "name": "...", "memory_mb": 16384 },
    "npu": { "name": "...", "available": true },
    "collected_at": "2026-06-17T10:30:00"
  }
}
```

---

#### GET /monitor/dynamic_info

**Purpose:** Return dynamic system metrics snapshot. Auto-cached with background refresh every 2 seconds.

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved dynamic system info",
  "data": {
    "collected_at": "2026-06-17 10:30:00",
    "cpu": { "usage_total": 35.2, "per_core_usage": [...], "per_core_freq_mhz": [...], "p_core_usage": 45.0, "e_core_usage": 20.0, "lpe_core_usage": 10.0, "temperature_c": 65.0, "per_core_temperature_c": [...], "..." },
    "memory": { "usage_percent": 50.0, "total_gb": 64.0, "available_gb": 32.0, "swap_total_gb": 8.0, "swap_used_gb": 1.2 },
    "pressure": { "level": "medium", "score": 45.2, "cpu": 12.5, "memory": 8.0, "io": 3.2, "network_busy_level": "medium", "..." },
    "network": { "per_nic": {"eth0": {"tx_mbps": 120.5, "rx_mbps": 85.3}} },
    "disk": { "is_stressed": false, "iowait": 2.1, "busy_level": "low", "disk_io": {"sda": {"read_mbps": 50.0, "write_mbps": 30.0, "is_busy": false}}, "..." },
    "gpu": { "vram": {"card0": {"used_mb": 1024, "total_mb": 8192, "free_mb": 7168}}, "gpu_usage": {"devices": [{"engines": ["rcs","bcs","vcs","vecs","ccs"], "engine_util": {"rcs": 45.2, "vcs": 80.5, "..."}, "freqs": [{"name": "gt0", "cur_mhz": 1200, "act_mhz": 1150, "max_mhz": 1500, "rc6_pct": 85.0}], "power_w": {"gpu": 15.2, "pkg": 28.0}}]} },
    "npu": { "npu_smi": {} }
  }
}
```

---

### History & Retention

#### GET /monitor/history

**Purpose:** Query monitor snapshot history with time-range filtering.

**Query Parameters:**

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| snapshot_type | No | string | "all" | Filter: `static`, `dynamic`, or `all` |
| limit | No | int | 100 | Max rows to return (1-20000) |
| start_time | No | int | — | Unix timestamp (seconds), range start |
| end_time | No | int | — | Unix timestamp (seconds), range end |
| range_seconds | No | int | — | Window length anchored to server clock (avoids clock-skew issues) |

**Notes:**
- `range_seconds` is only used when both `start_time` and `end_time` are omitted
- Server anchors the window to its own clock to avoid client clock-skew issues

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved monitor history",
  "data": {
    "snapshot_type": "dynamic",
    "limit": 100,
    "start_time": 1718600000,
    "end_time": 1718603600,
    "server_time": 1718603650,
    "count": 50,
    "items": [
      {
        "id": 1,
        "snapshot_type": "dynamic",
        "source": "auto",
        "collected_at": 1718600120,
        "create_time": 1718600120,
        "update_time": 1718600120,
        "create_date": "2026-06-17",
        "update_date": "2026-06-17",
        "data": { "cpu": { ... }, "memory": { ... } }
      }
    ]
  }
}
```

---

#### GET /monitor/history/retention

**Purpose:** Get current MonitorSnapshot retention period and allowed options.

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved retention settings",
  "data": {
    "retention_days": 3,
    "default_days": 3,
    "min_days": 1,
    "max_days": 7,
    "updated_at": 1718600000
  }
}
```

---

#### POST /monitor/history/retention

**Purpose:** Update the MonitorSnapshot retention period and trigger immediate cleanup.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | retention_days | Yes | int | Retention period (1-7 days) |
| Body | expected_updated_at | No | int | Unix timestamp from prior GET (optimistic concurrency) |

**Request Example:**
```json
{
  "retention_days": 5,
  "expected_updated_at": 1718600000
}
```

**Response (Success):**
```json
{
  "retcode": 0,
  "retmsg": "Retention set to 5 day(s)",
  "data": {
    "retention_days": 5,
    "deleted": 120,
    "updated_at": 1718603700
  }
}
```

**Response (409 Conflict):**
```json
{
  "retcode": 409,
  "retmsg": "Retention was modified by another client; please reload.",
  "data": {
    "current": {
      "retention_days": 3,
      "default_days": 3,
      "min_days": 1,
      "max_days": 7,
      "updated_at": 1718602500
    }
  }
}
```

---

### Configuration

#### GET /monitor/config/weights_top

**Purpose:** Get current ranking weights configuration (used for app resource scoring).

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved weights_top configuration",
  "data": {
    "cpu": 40,
    "memory": 30,
    "io": 10,
    "gpu": 20,
    "updated_at": 1718600000
  }
}
```

---

#### POST /monitor/config/weights_top

**Purpose:** Update ranking weights configuration with optimistic concurrency control.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | cpu | No | int | CPU weight (non-negative) |
| Body | memory | No | int | Memory weight (non-negative) |
| Body | gpu | No | int | GPU weight (non-negative) |
| Body | expected_updated_at | No | int | Unix timestamp from prior GET |

**Note:** I/O weight is not configurable via this API — disk I/O ranking uses pure throughput (MB/s).

**Request Example:**
```json
{
  "cpu": 50,
  "memory": 30,
  "gpu": 20,
  "expected_updated_at": 1718600000
}
```

**Response (Success):**
```json
{
  "retcode": 0,
  "retmsg": "Successfully updated weights_top configuration",
  "data": {
    "success": true,
    "updated_weights": {
      "cpu": 50,
      "memory": 30,
      "gpu": 20,
      "updated_at": 1718603800
    },
    "updated_at": 1718603800
  }
}
```

**Response (409 Conflict):**
```json
{
  "retcode": 409,
  "retmsg": "Configuration was modified by another client; please reload.",
  "data": {
    "success": false,
    "current": {
      "cpu": 40,
      "memory": 30,
      "gpu": 20,
      "updated_at": 1718602500
    }
  }
}
```

---

#### GET /monitor/config/passive_control

**Purpose:** Get the current passive resource-control switch state. When disabled, the balancer skips pressure-driven auto-limit/auto-restore; manual per-app limits and network controller remain active.

**Response:**
```json
{
  "retcode": 0,
  "retmsg": "Successfully retrieved passive_resource_control configuration",
  "data": {
    "enabled": true,
    "updated_at": 1718600000
  }
}
```

---

#### POST /monitor/config/passive_control

**Purpose:** Toggle the passive resource-control switch with optimistic concurrency.

**Request:**

| Type | Parameter | Required | Format | Description |
|------|-----------|----------|--------|-------------|
| Body | enabled | Yes | boolean | Enable/disable passive control |
| Body | expected_updated_at | No | int | Unix timestamp from prior GET |

**Request Example:**
```json
{
  "enabled": false,
  "expected_updated_at": 1718600000
}
```

**Response (Success):**
```json
{
  "retcode": 0,
  "retmsg": "Successfully updated passive_resource_control configuration",
  "data": {
    "success": true,
    "enabled": false,
    "updated_at": 1718603900
  }
}
```

**Response (409 Conflict):**
```json
{
  "retcode": 409,
  "retmsg": "Configuration was modified by another client; please reload.",
  "data": {
    "success": false,
    "current": {
      "enabled": true,
      "updated_at": 1718602500
    }
  }
}
```

---

## Notes

### Background Caching

Several monitor endpoints use background threads for performance:
- `/monitor/dynamic_info` — refreshes every 2 seconds
- `/monitor/app_resource_stats` — refreshes every 2 seconds (parks when idle >5.5s)
- `/monitor/app_disk_io_stats` — refreshes every 2 seconds (same cache thread)

### SSE Connection

The `/app/events` endpoint maintains a persistent connection:
- Sends a `{"type": "connected"}` event on connection
- Sends heartbeat comments every 30 seconds during idle periods
- App status change events include: `app_id`, `app_name`, `status`, `purpose`
