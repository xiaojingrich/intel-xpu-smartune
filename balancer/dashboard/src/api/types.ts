export interface ApiResponse<T> {
  retcode: number
  retmsg: string
  data: T
}

export interface CpuData {
  count: number
  usage: number
  available: number
  is_busy: boolean
}

export interface MemoryData {
  total_gb: number
  usage: number
  available_ratio: number
  is_busy: boolean
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

export interface SummaryData {
  cpu?: CpuData
  memory?: MemoryData
  disk?: DiskData
  network?: NetworkData
  pressure?: PressureData
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
