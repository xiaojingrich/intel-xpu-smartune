import React, { useState, useCallback } from 'react'
import { Row, Col, Card, Typography, Progress, Tag, Spin, Alert } from 'antd'
import {
  WifiOutlined,
  HddOutlined,
  ApiOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  DeploymentUnitOutlined,
} from '@ant-design/icons'
import { COLORS } from '../styles/theme'
import { api } from '../api/client'
import type { CpuData, MemoryData, DiskData, NetworkData } from '../api/types'
import { usePolling } from '../hooks/usePolling'

const { Text } = Typography

// ─── Helpers ─────────────────────────────────────────────────────────────────

function usageColor(ratio: number): string {
  if (ratio > 0.8) return COLORS.red
  if (ratio > 0.6) return COLORS.orange
  if (ratio > 0.3) return COLORS.yellow
  return COLORS.green
}

// ─── Sub-components ──────────────────────────────────────────────────────────

/** Circular gauge used in the top summary row */
function SummaryGauge({
  label,
  icon,
  value,
  unit = '%',
  color,
}: {
  label: string
  icon: React.ReactNode
  value: number | null
  unit?: string
  color: string
}) {
  const pct = value !== null ? Math.round(value * 100) : null
  return (
    <div style={{ textAlign: 'center' }}>
      <Progress
        type="dashboard"
        percent={pct ?? 0}
        strokeColor={color}
        trailColor={COLORS.border}
        size={80}
        format={() =>
          pct !== null ? (
            <span style={{ color, fontSize: 14, fontWeight: 700 }}>
              {pct}
              <span style={{ fontSize: 10, fontWeight: 400 }}>{unit}</span>
            </span>
          ) : (
            <span style={{ color: COLORS.textMuted, fontSize: 11 }}>—</span>
          )
        }
      />
      <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
        <span style={{ color: COLORS.textMuted, fontSize: 13 }}>{icon}</span>
        <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>{label}</Text>
      </div>
    </div>
  )
}

function MetricRow({ label, value, unit }: { label: string; value: React.ReactNode; unit?: string }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '4px 0',
        borderBottom: `1px solid ${COLORS.border}22`,
      }}
    >
      <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>{label}</Text>
      <Text style={{ color: COLORS.text, fontSize: 12, fontWeight: 500 }}>
        {value}
        {unit && <span style={{ color: COLORS.textMuted, marginLeft: 2 }}>{unit}</span>}
      </Text>
    </div>
  )
}

function UsageBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.round(value * 100)
  const color = usageColor(value)
  return (
    <div style={{ marginBottom: 6 }}>
      {label && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
          <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>{label}</Text>
          <Text style={{ color, fontSize: 11, fontWeight: 600 }}>{pct}%</Text>
        </div>
      )}
      <Progress
        percent={pct}
        showInfo={!label}
        strokeColor={color}
        trailColor={COLORS.border}
        size="small"
        style={{ margin: 0 }}
        format={(p) => <span style={{ color, fontSize: 11 }}>{p}%</span>}
      />
    </div>
  )
}

function PanelCard({
  title,
  icon,
  children,
  loading,
  error,
  extra,
}: {
  title: string
  icon?: React.ReactNode
  children: React.ReactNode
  loading?: boolean
  error?: string | null
  extra?: React.ReactNode
}) {
  return (
    <Card
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {icon}
          <span style={{ color: COLORS.text, fontSize: 13, fontWeight: 600 }}>{title}</span>
        </div>
      }
      extra={extra}
      style={{ background: COLORS.panelBg, border: `1px solid ${COLORS.border}`, borderRadius: 6, height: '100%' }}
      headStyle={{ borderBottom: `1px solid ${COLORS.border}`, padding: '8px 16px', minHeight: 40 }}
      bodyStyle={{ padding: '12px 16px' }}
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 20 }}>
          <Spin size="small" />
        </div>
      ) : error ? (
        <Alert message="Connection Error" description={error} type="error" showIcon style={{ fontSize: 12 }} />
      ) : (
        children
      )}
    </Card>
  )
}

function UnavailableCard({ title, icon, rows }: { title: string; icon: React.ReactNode; rows: string[] }) {
  return (
    <PanelCard title={title} icon={icon} extra={<Tag style={{ fontSize: 10 }}>N/A</Tag>}>
      <div style={{ textAlign: 'center', padding: '8px 0 12px' }}>
        <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>
          Hardware monitoring data not available
        </Text>
      </div>
      {rows.map((r) => (
        <MetricRow key={r} label={r} value="N/A" />
      ))}
    </PanelCard>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

interface Props {
  active: boolean
}

export default function SystemOverview({ active }: Props) {
  const [cpu, setCpu] = useState<CpuData | null>(null)
  const [memory, setMemory] = useState<MemoryData | null>(null)
  const [disk, setDisk] = useState<DiskData | null>(null)
  const [network, setNetwork] = useState<NetworkData | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const fetchAll = useCallback(async () => {
    const results = await Promise.allSettled([
      api.getCpu(),
      api.getMemory(),
      api.getDisk(),
      api.getNetwork(),
    ])

    const errs: Record<string, string> = {}

    if (results[0].status === 'fulfilled') setCpu(results[0].value)
    else errs.cpu = results[0].reason?.message ?? 'Failed'

    if (results[1].status === 'fulfilled') setMemory(results[1].value)
    else errs.memory = results[1].reason?.message ?? 'Failed'

    if (results[2].status === 'fulfilled') setDisk(results[2].value)
    else errs.disk = results[2].reason?.message ?? 'Failed'

    if (results[3].status === 'fulfilled') setNetwork(results[3].value)
    else errs.network = results[3].reason?.message ?? 'Failed'

    setErrors(errs)
    setLoading(false)
  }, [])

  usePolling(fetchAll, 3000, active)

  const diskDevices = disk?.disk_io ? Object.entries(disk.disk_io) : []
  const maxDiskUtil = diskDevices.length
    ? Math.max(...diskDevices.map(([, d]) => d.utilization))
    : null
  const avgNetwork = network ? (network.rx + network.tx) / 2 : null

  return (
    <div style={{ padding: '16px 0' }}>

      {/* ── Summary row ─────────────────────────────────────────────────── */}
      <Card
        style={{
          background: COLORS.panelBg,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 6,
          marginBottom: 12,
        }}
        bodyStyle={{ padding: '16px 24px' }}
      >
        <Row justify="space-around" align="middle" gutter={[16, 16]}>
          <Col>
            <SummaryGauge
              label="CPU"
              icon={<ThunderboltOutlined />}
              value={cpu?.usage ?? null}
              color={usageColor(cpu?.usage ?? 0)}
            />
          </Col>
          <Col>
            <SummaryGauge
              label="Memory"
              icon={<ApiOutlined />}
              value={memory?.usage ?? null}
              color={usageColor(memory?.usage ?? 0)}
            />
          </Col>
          <Col>
            <SummaryGauge
              label="Disk IO"
              icon={<HddOutlined />}
              value={maxDiskUtil}
              color={usageColor(maxDiskUtil ?? 0)}
            />
          </Col>
          <Col>
            <SummaryGauge
              label="Network"
              icon={<WifiOutlined />}
              value={avgNetwork}
              color={usageColor(avgNetwork ?? 0)}
            />
          </Col>
          {cpu && (
            <Col>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: COLORS.text, lineHeight: 1 }}>
                  {cpu.count}
                </div>
                <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>CPU Cores</Text>
              </div>
            </Col>
          )}
          {memory && (
            <Col>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: COLORS.text, lineHeight: 1 }}>
                  {memory.total_gb.toFixed(0)}
                  <span style={{ fontSize: 12, fontWeight: 400, color: COLORS.textMuted }}> GB</span>
                </div>
                <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>System RAM</Text>
              </div>
            </Col>
          )}
        </Row>
      </Card>

      {/* ── Detail cards ──────────────────────────────────────────────────── */}
      <Row gutter={[12, 12]}>

        {/* CPU */}
        <Col xs={24} sm={12} xl={6}>
          <PanelCard
            title="CPU"
            icon={<ThunderboltOutlined style={{ color: COLORS.accent }} />}
            loading={loading && !cpu}
            error={errors.cpu}
            extra={cpu && (
              <Tag color={cpu.is_busy ? 'error' : 'success'} style={{ fontSize: 10 }}>
                {cpu.is_busy ? 'BUSY' : 'OK'}
              </Tag>
            )}
          >
            {cpu && (
              <>
                <MetricRow label="Core Count" value={cpu.count} />
                <MetricRow label="Usage" value={`${(cpu.usage * 100).toFixed(1)}`} unit="%" />
                <MetricRow label="Available" value={`${(cpu.available * 100).toFixed(1)}`} unit="%" />
                <div style={{ marginTop: 10 }}>
                  <UsageBar value={cpu.usage} label="Overall Usage" />
                </div>
              </>
            )}
          </PanelCard>
        </Col>

        {/* Memory */}
        <Col xs={24} sm={12} xl={6}>
          <PanelCard
            title="System Memory"
            icon={<ApiOutlined style={{ color: '#f7c948' }} />}
            loading={loading && !memory}
            error={errors.memory}
            extra={memory && (
              <Tag color={memory.is_busy ? 'error' : 'success'} style={{ fontSize: 10 }}>
                {memory.is_busy ? 'BUSY' : 'OK'}
              </Tag>
            )}
          >
            {memory && (
              <>
                <MetricRow label="Total" value={memory.total_gb.toFixed(1)} unit="GB" />
                <MetricRow label="Usage" value={`${(memory.usage * 100).toFixed(1)}`} unit="%" />
                <MetricRow label="Available" value={`${(memory.available_ratio * 100).toFixed(1)}`} unit="%" />
                <div style={{ marginTop: 10 }}>
                  <UsageBar value={memory.usage} label="Memory Usage" />
                </div>
              </>
            )}
          </PanelCard>
        </Col>

        {/* Disk IO */}
        <Col xs={24} sm={12} xl={6}>
          <PanelCard
            title="Disk IO"
            icon={<HddOutlined style={{ color: '#e05c5c' }} />}
            loading={loading && !disk}
            error={errors.disk}
          >
            {diskDevices.length === 0 && !loading ? (
              <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>No disk devices found</Text>
            ) : (
              diskDevices.map(([device, data]) => (
                <div key={device} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text style={{ color: COLORS.accent, fontSize: 12, fontWeight: 600 }}>{device}</Text>
                    <Tag color={data.is_busy ? 'error' : 'success'} style={{ fontSize: 10, margin: 0 }}>
                      {data.is_busy ? 'BUSY' : 'IDLE'}
                    </Tag>
                  </div>
                  <MetricRow label="Read" value={data.read_kb_per_sec.toFixed(1)} unit="KB/s" />
                  <MetricRow label="Write" value={data.write_kb_per_sec.toFixed(1)} unit="KB/s" />
                  <UsageBar value={data.utilization} label="Utilization" />
                </div>
              ))
            )}
          </PanelCard>
        </Col>

        {/* Network IO */}
        <Col xs={24} sm={12} xl={6}>
          <PanelCard
            title="Network IO"
            icon={<WifiOutlined style={{ color: '#52c41a' }} />}
            loading={loading && !network}
            error={errors.network}
          >
            {network && (
              <>
                <div style={{ marginBottom: 10 }}>
                  <UsageBar value={network.rx} label="RX" />
                  <UsageBar value={network.tx} label="TX" />
                </div>
                <MetricRow label="RX" value={`${(network.rx * 100).toFixed(1)}`} unit="% BW" />
                <MetricRow label="TX" value={`${(network.tx * 100).toFixed(1)}`} unit="% BW" />
              </>
            )}
          </PanelCard>
        </Col>

        {/* iGPU */}
        <Col xs={24} sm={12} xl={8}>
          <UnavailableCard
            title="iGPU (Integrated)"
            icon={<ThunderboltOutlined style={{ color: '#a855f7' }} />}
            rows={['Name', 'EU Count', 'Min Freq', 'Max Freq', 'TDP', 'RCS Usage', 'VCS Usage', 'Power']}
          />
        </Col>

        {/* dGPU */}
        <Col xs={24} sm={12} xl={8}>
          <UnavailableCard
            title="dGPU (Discrete)"
            icon={<DeploymentUnitOutlined style={{ color: '#f59e0b' }} />}
            rows={['Name', 'Count', 'Min Freq', 'Max Freq', 'TDP', 'BCS Usage', 'CCS Usage', 'Power']}
          />
        </Col>

        {/* NPU */}
        <Col xs={24} sm={12} xl={8}>
          <UnavailableCard
            title="NPU"
            icon={<RobotOutlined style={{ color: '#06b6d4' }} />}
            rows={['Min Freq', 'Max Freq', 'Tile Count', 'Usage', 'Power', 'Thermal']}
          />
        </Col>

      </Row>
    </div>
  )
}
