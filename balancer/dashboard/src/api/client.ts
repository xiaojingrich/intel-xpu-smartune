import axios from 'axios'
import type {
  ApiResponse,
  CpuData,
  MemoryData,
  DiskData,
  NetworkData,
  PressureData,
  SummaryData,
  TopConsumersData,
  AppListData,
  SetControlPayload,
  AppIdPayload,
  SetPriorityPayload,
  ResourceLimitPayload,
} from './types'

const client = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
})

async function get<T>(url: string): Promise<T> {
  const res = await client.get<ApiResponse<T>>(url)
  if (res.data.retcode !== 0) throw new Error(res.data.retmsg)
  return res.data.data
}

async function post<T>(url: string, body: object = {}): Promise<T> {
  const res = await client.post<ApiResponse<T>>(url, body)
  if (res.data.retcode !== 0) throw new Error(res.data.retmsg)
  return res.data.data
}

export const api = {
  getCpu: () => get<CpuData>('/monitor/cpu'),
  getMemory: () => get<MemoryData>('/monitor/memory'),
  getDisk: () => get<DiskData>('/monitor/disk'),
  getNetwork: () => get<NetworkData>('/monitor/network'),
  getPressure: () => get<PressureData>('/monitor/pressure'),
  getSummary: () => get<SummaryData>('/monitor/summary'),
  getTopConsumers: () => get<TopConsumersData>('/monitor/top_consumers'),

  getApps: () => post<AppListData>('/app/get_apps'),
  getControlledApps: () => post<AppListData>('/app/get_controlled_app'),
  getPendingApps: () => post<AppListData>('/app/get_pending_app'),

  setToControl: (payload: SetControlPayload) =>
    post<void>('/app/set_to_control', payload),
  removeFromControl: (payload: AppIdPayload) =>
    post<void>('/app/remove_from_control', payload),
  setPriority: (payload: SetPriorityPayload) =>
    post<void>('/app/set_priority', payload),
  setOomScore: (payload: Pick<AppIdPayload, 'app_id'>) =>
    post<void>('/app/set_oom_score', payload),
  cancelRelaunch: (payload: Pick<AppIdPayload, 'app_id'>) =>
    post<void>('/app/cancel_relaunch', payload),
  resourceLimit: (payload: ResourceLimitPayload) =>
    post<void>('/app/resource_limit', payload),
  resourceRestore: (payload: Pick<AppIdPayload, 'app_id'>) =>
    post<void>('/app/resource_restore', payload),
}
