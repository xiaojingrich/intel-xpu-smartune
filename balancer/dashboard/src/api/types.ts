export interface ApiResponse<T> {
  retcode: number
  retmsg: string
  data: T
}

export interface DiskDeviceData {
  utilization: number
  is_busy: boolean
  read_kb_per_sec: number
  write_kb_per_sec: number
}

export interface DiskData {
  disk_io: Record<string, DiskDeviceData>
}

export interface NetworkData {
  rx: number
  tx: number
}

export interface PressureData {
  cpu: number
  memory: number
  io: number
}

export interface ProcessInfo {
  pid: number
  name: string
  cmdline: string
  cpu_avg: number
  memory_rss: number
  io_read_rate: number
  score?: number
}

export interface AppInfo {
  app_id: string
  app_name: string
  cpu_usage: number
  memory_mb: number
  io_read_rate: number
  score?: number
  priority?: string
  status?: string
  controlled?: boolean
  remark?: string
  cmdline?: string
  cgroup?: string
  is_running?: boolean
  is_pending?: boolean
}

export interface Consumer {
  process: ProcessInfo
  app: AppInfo
}

export interface TopConsumersData {
  consumers: Consumer[]
  reach_threshold: boolean
}

export type AppListData = AppInfo[]

export interface SetControlPayload {
  app_id: string
  app_name: string
  priority: string
  controlled: boolean
  remark: string
  cmdline: string
  cgroup: string
}

export interface AppIdPayload {
  app_id: string
  app_name: string
}

export interface SetPriorityPayload {
  app_id: string
  priority: string
}

export interface ResourceLimitPayload {
  app_id: string
  app_name: string
  priority: string
}

export interface PackageInfo {
  installed: boolean
  version: string | null
  raw: string | null
}

export interface StaticInfoData {
  collected_at: string
  bios: {
    version: string | null
  }
  os: {
    version: string | null
  }
  driver: {
    kernel_version: string | null
    kernel_cmdline: string | null
    mesa: PackageInfo
    opencl: PackageInfo
    level_zero: PackageInfo
    media: PackageInfo
    npu_fw: string | null
  }
  cpu: {
    model_name: string | null
    core_count: {
      logical: number | null
      physical: number | null
    }
    freq_mhz: {
      min_mhz: number | null
      max_mhz: number | null
      per_core_mhz: Array<number | null>
    }
  }
  memory: {
    ddr_speeds: string[]
    total_gb: number | null
  }
  io: {
    nic_count: number
    network_speeds_mbps: Record<string, number>
    network_peak_mbps: number | null
    primary_interface: string
  }
  disk: {
    device_count: number
    total_size_bytes: number | null
    total_size_gb: number | null
    devices: Array<{
      name: string
      size_bytes: number | null
      size_gb: number | null
    }>
  }
  gpu: {
    names: string[]
    count: number
    engines: Record<string, string[]>
    freq_bounds_mhz: Record<string, { min_mhz: number | null; max_mhz: number | null }>
    vram: Record<string, { total_bytes: number | null; used_bytes: number | null; usage_percent: number | null }>
    pcie: Record<string, { current_speed: string | null; current_width: string | null; max_speed: string | null; max_width: string | null }>
  }
  npu: {
    names: string[]
    freq_bounds_mhz: Record<string, { min_mhz: number | null; max_mhz: number | null }>
  }
}

export interface ToolOutput {
  available: boolean
  raw: string | null
  error: string | null
}

export interface QmassaFreq {
  name: string
  min_mhz: number | null
  cur_mhz: number | null
  act_mhz: number | null
  max_mhz: number | null
  throttled: boolean
  throttle_reasons: string[]
}

export interface QmassaDevice {
  pci_dev: string | null
  dev_type: string | null
  drv_name: string | null
  engines: string[]
  freqs: QmassaFreq[]
  power_w: {
    gpu: number | null
    pkg: number | null
  }
  engine_util: Record<string, number | null>
}

export interface QmassaParsed {
  timestamp: number | null
  version: string | null
  devices: QmassaDevice[]
}

export interface QmassaOutput {
  available: boolean
  raw: string | null
  error: string | null
  parsed: QmassaParsed | null
}

export interface DynamicInfoData {
  collected_at: string
  cpu: {
    usage_total: number | null
    per_core_usage: number[]
    per_core_freq_mhz: Array<number | null>
    p_core_usage: number | null
    e_core_usage: number | null
    p_core_freq_mhz: number | null
    e_core_freq_mhz: number | null
    p_core_indices: number[]
    e_core_indices: number[]
    core_type_source: string
  }
  memory: {
    usage_percent: number | null
    total_gb: number | null
    available_gb: number | null
  }
  pressure: PressureData
  network: {
    interfaces: Record<string, { rx_bytes_per_sec: number; tx_bytes_per_sec: number }>
    total: { rx_bytes_per_sec: number; tx_bytes_per_sec: number }
  }
  disk: DiskData
  gpu: {
    vram: Record<string, { total_bytes: number | null; used_bytes: number | null; usage_percent: number | null }>
    qmassa: QmassaOutput
  }
  npu: {
    npu_smi: ToolOutput
  }
}

export type HistorySnapshotType = 'static' | 'dynamic' | 'all'

export interface HistorySnapshotItem {
  id: number
  snapshot_type: 'static' | 'dynamic'
  source: string
  collected_at: string | null
  create_time: number
  update_time: number
  create_date: string | null
  update_date: string | null
  data: StaticInfoData | DynamicInfoData | Record<string, unknown> | string | null
}

export interface HistoryData {
  snapshot_type: HistorySnapshotType
  limit: number
  start_time?: number | null
  end_time?: number | null
  count: number
  items: HistorySnapshotItem[]
}

export interface HistoryQueryOptions {
  snapshotType?: HistorySnapshotType
  limit?: number
  startTime?: number | null
  endTime?: number | null
}
