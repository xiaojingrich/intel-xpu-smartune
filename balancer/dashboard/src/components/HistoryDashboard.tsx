import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, DatePicker, Empty, Segmented, Select, Space, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  Brush,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import dayjs, { type Dayjs } from 'dayjs'
import { api } from '../api/client'
import type { DynamicInfoData, HistoryData, HistorySnapshotItem, QmassaDevice } from '../api/types'
import { COLORS } from '../styles/theme'

const { Text, Title } = Typography

interface Props {
  active: boolean
}

type EngineKey = 'vcs' | 'vecs' | 'ccs' | 'rcs' | 'bcs'

interface CommonTrendPoint {
  timestamp: string
  ts: number
  systemPressure: number | null
  systemDisk: number | null
  systemNetwork: number | null
  cpuUtilization: number | null
  memoryUtilization: number | null
  diskUtilization: number | null
  networkUtilization: number | null
  npuUtilization: number | null
}

interface GpuTrendPoint {
  timestamp: string
  ts: number
  vcs: number | null
  vecs: number | null
  ccs: number | null
  rcs: number | null
  bcs: number | null
  gt0Freq: number | null
  gt1Freq: number | null
}

interface GpuTrendSeries {
  id: string
  label: string
  points: GpuTrendPoint[]
}

type RangePreset = '5m' | '15m' | '1h' | '6h' | '24h' | 'custom'

interface HistoryNetworkExtra {
  utilization_percent?: number | null
}

interface HistoryDiskExtra {
  utilization?: number | null
}

interface HistoryNpuSmiExtra {
  utilization_percent?: number | null
  parsed?: {
    utilization?: number | null
    utilization_percent?: number | null
  } | null
}

const LIMIT_OPTIONS = [50, 100, 200, 500]
const RANGE_PRESET_OPTIONS: Array<{ label: string; value: RangePreset }> = [
  { label: '5m', value: '5m' },
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '6h', value: '6h' },
  { label: '24h', value: '24h' },
  { label: 'Custom', value: 'custom' },
]

const RANGE_SECONDS: Record<Exclude<RangePreset, 'custom'>, number> = {
  '5m': 5 * 60,
  '15m': 15 * 60,
  '1h': 60 * 60,
  '6h': 6 * 60 * 60,
  '24h': 24 * 60 * 60,
}

const ENGINE_CURVE_META: Array<{ key: EngineKey; name: string; color: string }> = [
  { key: 'vcs', name: 'VCS %', color: COLORS.accent },
  { key: 'vecs', name: 'VECS %', color: COLORS.green },
  { key: 'ccs', name: 'CCS %', color: COLORS.yellow },
  { key: 'rcs', name: 'RCS %', color: COLORS.orange },
  { key: 'bcs', name: 'BCS %', color: COLORS.red },
]

function normalizePercent(value: unknown): number | null {
  if (typeof value !== 'number' || Number.isNaN(value)) return null
  if (value <= 1) return Math.max(0, Math.min(value * 100, 100))
  return Math.max(0, Math.min(value, 100))
}

function toNumber(value: unknown): number | null {
  if (typeof value !== 'number' || Number.isNaN(value)) return null
  return value
}

function isNumber(value: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function toDynamicData(item: HistorySnapshotItem): DynamicInfoData | null {
  if (item.snapshot_type !== 'dynamic') return null
  if (!item.data || typeof item.data !== 'object' || Array.isArray(item.data)) return null
  return item.data as DynamicInfoData
}

function buildTimestamp(item: HistorySnapshotItem): { ts: number; label: string } {
  const tsSec = Number.isFinite(item.create_time) ? item.create_time : 0
  const label = tsSec > 0
    ? dayjs.unix(tsSec).format('MM-DD HH:mm:ss')
    : (item.collected_at || item.create_date || `#${item.id}`)
  return { ts: tsSec, label }
}

function getPressurePeak(dynamic: DynamicInfoData | null): number | null {
  const pressureCpu = normalizePercent(dynamic?.pressure?.cpu)
  const pressureMemory = normalizePercent(dynamic?.pressure?.memory)
  const pressureIo = normalizePercent(dynamic?.pressure?.io)
  const values = [pressureCpu, pressureMemory, pressureIo].filter(isNumber)
  if (!values.length) return null
  return Math.max(...values)
}

function getDiskUsage(dynamic: DynamicInfoData | null): number | null {
  if (!dynamic?.disk) return null
  const disk = dynamic.disk as DynamicInfoData['disk'] & HistoryDiskExtra

  const summarized = normalizePercent(disk.utilization)
  if (summarized != null) return summarized

  const diskItems = Object.values(disk.disk_io || {})
  const values = diskItems
    .map((item) => normalizePercent(item?.utilization))
    .filter(isNumber)
  if (!values.length) return null
  return Math.max(...values)
}

function getNetworkUsage(dynamic: DynamicInfoData | null): number | null {
  if (!dynamic?.network) return null
  const network = dynamic.network as DynamicInfoData['network'] & HistoryNetworkExtra
  return normalizePercent(network.utilization_percent)
}

function getNpuUsage(dynamic: DynamicInfoData | null): number | null {
  if (!dynamic?.npu?.npu_smi) return null
  const npuSmi = dynamic.npu.npu_smi as DynamicInfoData['npu']['npu_smi'] & HistoryNpuSmiExtra
  const direct = normalizePercent(npuSmi.utilization_percent)
  if (direct != null) return direct
  return normalizePercent(npuSmi.parsed?.utilization_percent ?? npuSmi.parsed?.utilization)
}

function normalizeEngineUtil(device: QmassaDevice, key: EngineKey): number | null {
  return normalizePercent(device.engine_util?.[key])
}

function getFreq(device: QmassaDevice, targetName: 'gt0' | 'gt1'): number | null {
  const freq = (device.freqs || []).find((item) => item.name === targetName)
  if (!freq) return null
  return toNumber(freq.cur_mhz ?? freq.act_mhz)
}

function getGpuLabel(device: QmassaDevice, index: number): string {
  const type = `${device.dev_type || ''}`.toLowerCase()
  const role = type.includes('integrated') || type.includes('igpu')
    ? 'iGPU'
    : (type.includes('discrete') || type.includes('dgpu') ? 'dGPU' : `GPU${index}`)
  const pci = device.pci_dev ? ` (${device.pci_dev})` : ''
  return `${role}${pci}`
}

function buildCommonTrendPoints(items: HistorySnapshotItem[]): CommonTrendPoint[] {
  return [...items]
    .reverse()
    .map((item) => {
      const dynamic = toDynamicData(item)
      const { ts, label } = buildTimestamp(item)
      const pressureIo = normalizePercent(dynamic?.pressure?.io)
      const networkUsage = getNetworkUsage(dynamic)
      const diskUsage = getDiskUsage(dynamic)
      return {
        timestamp: label,
        ts,
        systemPressure: getPressurePeak(dynamic),
        systemDisk: pressureIo,
        systemNetwork: networkUsage,
        cpuUtilization: normalizePercent(dynamic?.cpu?.usage_total),
        memoryUtilization: normalizePercent(dynamic?.memory?.usage_percent),
        diskUtilization: diskUsage,
        networkUtilization: networkUsage,
        npuUtilization: getNpuUsage(dynamic),
      }
    })
}

function buildGpuTrendSeries(items: HistorySnapshotItem[]): GpuTrendSeries[] {
  const seriesMap = new Map<string, GpuTrendSeries>()

  for (const item of [...items].reverse()) {
    const dynamic = toDynamicData(item)
    if (!dynamic) continue
    const devices = dynamic.gpu?.qmassa?.parsed?.devices || []
    const { ts, label } = buildTimestamp(item)

    devices.forEach((device, index) => {
      const id = device.pci_dev || `${device.dev_type || 'gpu'}-${index}`
      const seriesLabel = getGpuLabel(device, index)

      if (!seriesMap.has(id)) {
        seriesMap.set(id, { id, label: seriesLabel, points: [] })
      }

      const point: GpuTrendPoint = {
        timestamp: label,
        ts,
        vcs: normalizeEngineUtil(device, 'vcs'),
        vecs: normalizeEngineUtil(device, 'vecs'),
        ccs: normalizeEngineUtil(device, 'ccs'),
        rcs: normalizeEngineUtil(device, 'rcs'),
        bcs: normalizeEngineUtil(device, 'bcs'),
        gt0Freq: getFreq(device, 'gt0'),
        gt1Freq: getFreq(device, 'gt1'),
      }

      seriesMap.get(id)?.points.push(point)
    })
  }

  return Array.from(seriesMap.values()).filter((series) => series.points.length > 0)
}

export default function HistoryDashboard({ active }: Props) {
  const [limit, setLimit] = useState<number>(100)
  const [rangePreset, setRangePreset] = useState<RangePreset>('15m')
  const [customRange, setCustomRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [history, setHistory] = useState<HistoryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastFetchAt, setLastFetchAt] = useState<string | null>(null)

  const fetchHistory = useCallback(async () => {
    if (!active) return
    setLoading(true)

    const nowSec = Math.floor(Date.now() / 1000)
    let startTime: number | null = null
    let endTime: number | null = null

    if (rangePreset === 'custom') {
      const start = customRange?.[0]
      const end = customRange?.[1]
      if (start && end) {
        startTime = start.unix()
        endTime = end.unix()
      }
    } else {
      const preset = rangePreset as Exclude<RangePreset, 'custom'>
      const seconds = RANGE_SECONDS[preset]
      startTime = nowSec - seconds
      endTime = nowSec
    }

    try {
      const data = await api.getHistory({
        snapshotType: 'dynamic',
        limit,
        startTime,
        endTime,
      })
      setHistory(data)
      setError(null)
      setLastFetchAt(dayjs().format('YYYY-MM-DD HH:mm:ss'))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch history data')
    } finally {
      setLoading(false)
    }
  }, [active, limit, rangePreset, customRange])

  useEffect(() => {
    if (!active) return
    fetchHistory()
  }, [active, fetchHistory])

  const dynamicItems = useMemo<HistorySnapshotItem[]>(
    () => (history?.items ?? []).filter((item: HistorySnapshotItem) => item.snapshot_type === 'dynamic'),
    [history],
  )

  const commonTrendPoints = useMemo(() => buildCommonTrendPoints(dynamicItems), [dynamicItems])
  const gpuTrendSeries = useMemo(() => buildGpuTrendSeries(dynamicItems), [dynamicItems])

  const rangeHint = useMemo(() => {
    if (rangePreset !== 'custom') return `Range: ${rangePreset}`
    const start = customRange?.[0]
    const end = customRange?.[1]
    if (!start || !end) return 'Range: custom (select start/end)'
    return `Range: ${start.format('MM-DD HH:mm')} ~ ${end.format('MM-DD HH:mm')}`
  }, [rangePreset, customRange])

  return (
    <div style={{ padding: '16px 0' }}>
      {error && (
        <Alert
          message="History API Error"
          description={error}
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <Title level={5} style={{ color: COLORS.text, margin: 0 }}>
          History Trends
        </Title>

        <Space size={12} wrap>
          <Segmented
            options={RANGE_PRESET_OPTIONS}
            value={rangePreset}
            onChange={(val: string | number) => setRangePreset(val as RangePreset)}
          />

          {rangePreset === 'custom' && (
            <DatePicker.RangePicker
              showTime
              allowClear
              value={customRange}
              onChange={(values: [Dayjs | null, Dayjs | null] | null) => {
                setCustomRange(values ?? null)
              }}
            />
          )}

          <Select
            value={limit}
            onChange={setLimit}
            style={{ width: 120 }}
            options={LIMIT_OPTIONS.map((val) => ({ label: `${val} rows`, value: val }))}
          />

          <Button icon={<ReloadOutlined />} onClick={() => fetchHistory()}>
            Manual Refresh
          </Button>
        </Space>
      </div>

      <Text style={{ color: COLORS.textMuted, fontSize: 12, display: 'block', marginBottom: 12 }}>
        {rangeHint}{lastFetchAt ? ` | Last fetch: ${lastFetchAt}` : ''}
      </Text>

      <Card
        style={{
          background: COLORS.panelBg,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 6,
        }}
        bodyStyle={{ padding: 16 }}
      >
        <Text style={{ color: COLORS.textMuted, display: 'block', marginBottom: 10 }}>
          History curves (System Pressure / Disk / Network + CPU / Memory / Disk / Network / NPU Utilization)
        </Text>

        <div style={{ width: '100%', height: 380 }}>
          {loading ? (
            <div style={{ color: COLORS.textMuted, paddingTop: 48, textAlign: 'center' }}>Loading history...</div>
          ) : commonTrendPoints.length === 0 ? (
            <Empty description="No history data" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={commonTrendPoints} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={`${COLORS.border}99`} strokeDasharray="3 3" />
                <XAxis
                  dataKey="timestamp"
                  tick={{ fill: COLORS.textMuted, fontSize: 11 }}
                  minTickGap={36}
                  tickFormatter={(val: string | number) => String(val).slice(-8)}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fill: COLORS.textMuted, fontSize: 11 }}
                  tickFormatter={(val: string | number) => `${val}%`}
                />
                <Tooltip
                  contentStyle={{
                    background: COLORS.panelBg,
                    border: `1px solid ${COLORS.border}`,
                    color: COLORS.text,
                  }}
                />
                <Legend wrapperStyle={{ color: COLORS.textMuted }} />
                <Line type="monotone" dataKey="systemPressure" name="System Pressure %" stroke={COLORS.orange} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="diskPressure" name="System Disk %" stroke={COLORS.red} strokeDasharray="5 3" dot={false} strokeWidth={1.9} />
                <Line type="monotone" dataKey="networkPressure" name="System Network %" stroke={COLORS.green} strokeDasharray="5 3" dot={false} strokeWidth={1.9} />
                <Line type="monotone" dataKey="cpuUtilization" name="CPU Utilization %" stroke={COLORS.accent} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="memoryUtilization" name="Memory Utilization %" stroke={COLORS.yellow} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="diskUtilization" name="Disk Utilization %" stroke={COLORS.red} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="networkUtilization" name="Network Utilization %" stroke={COLORS.green} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="npuUtilization" name="NPU Utilization %" stroke={COLORS.text} dot={false} strokeWidth={1.8} />
                <Brush
                  dataKey="timestamp"
                  height={24}
                  stroke={COLORS.accent}
                  travellerWidth={8}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>

      <div style={{ marginTop: 16 }}>
        {loading ? (
          <Card
            style={{
              background: COLORS.panelBg,
              border: `1px solid ${COLORS.border}`,
              borderRadius: 6,
            }}
            bodyStyle={{ padding: 16 }}
          >
            <div style={{ color: COLORS.textMuted, textAlign: 'center' }}>Loading GPU history...</div>
          </Card>
        ) : gpuTrendSeries.length === 0 ? (
          <Card
            style={{
              background: COLORS.panelBg,
              border: `1px solid ${COLORS.border}`,
              borderRadius: 6,
            }}
            bodyStyle={{ padding: 16 }}
          >
            <Empty description="No GPU history data" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </Card>
        ) : (
          gpuTrendSeries.map((series: GpuTrendSeries) => (
            <Card
              key={series.id}
              style={{
                background: COLORS.panelBg,
                border: `1px solid ${COLORS.border}`,
                borderRadius: 6,
                marginBottom: 16,
              }}
              bodyStyle={{ padding: 16 }}
            >
              <Text style={{ color: COLORS.textMuted, display: 'block', marginBottom: 10 }}>
                {series.label} - Engine Utilization & GT Frequency
              </Text>

              <div style={{ width: '100%', height: 340 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={series.points} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke={`${COLORS.border}99`} strokeDasharray="3 3" />
                    <XAxis
                      dataKey="timestamp"
                      tick={{ fill: COLORS.textMuted, fontSize: 11 }}
                      minTickGap={36}
                      tickFormatter={(val: string | number) => String(val).slice(-8)}
                    />
                    <YAxis
                      yAxisId="util"
                      domain={[0, 100]}
                      tick={{ fill: COLORS.textMuted, fontSize: 11 }}
                      tickFormatter={(val: string | number) => `${val}%`}
                    />
                    <YAxis
                      yAxisId="freq"
                      orientation="right"
                      tick={{ fill: COLORS.textMuted, fontSize: 11 }}
                      tickFormatter={(val: string | number) => `${val}`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: COLORS.panelBg,
                        border: `1px solid ${COLORS.border}`,
                        color: COLORS.text,
                      }}
                    />
                    <Legend wrapperStyle={{ color: COLORS.textMuted }} />

                    {ENGINE_CURVE_META.map((engine) => (
                      <Line
                        key={engine.key}
                        type="monotone"
                        yAxisId="util"
                        dataKey={engine.key}
                        name={engine.name}
                        stroke={engine.color}
                        dot={false}
                        strokeWidth={2}
                      />
                    ))}

                    <Line
                      type="monotone"
                      yAxisId="freq"
                      dataKey="gt0Freq"
                      name="GT0 MHz"
                      stroke={COLORS.text}
                      strokeDasharray="6 4"
                      dot={false}
                      strokeWidth={1.8}
                    />
                    <Line
                      type="monotone"
                      yAxisId="freq"
                      dataKey="gt1Freq"
                      name="GT1 MHz"
                      stroke={COLORS.textMuted}
                      strokeDasharray="4 4"
                      dot={false}
                      strokeWidth={1.8}
                    />

                    <Brush
                      dataKey="timestamp"
                      height={24}
                      stroke={COLORS.accent}
                      travellerWidth={8}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          ))
        )}
      </div>
    </div>
  )
}
