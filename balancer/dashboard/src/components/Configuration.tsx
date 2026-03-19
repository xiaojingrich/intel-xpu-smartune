import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Row, Col, Card, Typography, Descriptions, Tag, Alert, Spin, Button, Space } from 'antd'
import { SettingOutlined, ThunderboltOutlined, DatabaseOutlined, WifiOutlined, RobotOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import type { StaticInfoData, DynamicInfoData, PackageInfo, QmassaFreq } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { COLORS } from '../styles/theme'

const { Text, Title } = Typography

interface Props {
  active: boolean
}

function PanelCard({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {icon}
          <span style={{ color: COLORS.text, fontSize: 13, fontWeight: 600 }}>{title}</span>
        </div>
      }
      style={{ background: COLORS.panelBg, border: `1px solid ${COLORS.border}`, borderRadius: 6, height: '100%' }}
      headStyle={{ borderBottom: `1px solid ${COLORS.border}`, padding: '8px 16px', minHeight: 40 }}
      bodyStyle={{ padding: '12px 16px' }}
    >
      {children}
    </Card>
  )
}

function formatValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === '') {
    return <Tag style={{ fontSize: 10 }}>N/A</Tag>
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(', ') : <Tag style={{ fontSize: 10 }}>N/A</Tag>
  }
  if (typeof value === 'number') {
    return value
  }
  return value as React.ReactNode
}

function formatPackage(pkg: PackageInfo): React.ReactNode {
  if (!pkg?.installed) return <Tag style={{ fontSize: 10 }}>Not Installed</Tag>
  return pkg.version || pkg.raw || 'Installed'
}

function formatMap<K extends string, V>(
  data: Record<K, V> | undefined,
  formatter: (value: V, key: string) => string
): string {
  if (!data || Object.keys(data).length === 0) return 'N/A'
  return Object.entries(data)
    .map(([key, value]) => formatter(value as V, key))
    .join(' | ')
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

function formatWithMax(value?: number | null, max?: number | null, unit?: string): string {
  if (value === null || value === undefined) return 'N/A'
  if (max === null || max === undefined) return unit ? `${value} ${unit}` : `${value}`
  return unit ? `${value} / ${max} ${unit}` : `${value} / ${max}`
}

function formatThrottle(freq?: QmassaFreq): string {
  if (!freq) return 'N/A'
  if (!freq.throttled) return 'No'
  if (freq.throttle_reasons?.length) return `Yes (${freq.throttle_reasons.join(', ')})`
  return 'Yes'
}

function formatPercent(value?: number | null): string {
  if (value === null || value === undefined) return 'N/A'
  return `${value.toFixed(2)}%`
}

export default function Configuration({ active }: Props) {
  const [staticInfo, setStaticInfo] = useState<StaticInfoData | null>(null)
  const [dynamicInfo, setDynamicInfo] = useState<DynamicInfoData | null>(null)
  const [loadingStatic, setLoadingStatic] = useState(false)
  const [loadingDynamic, setLoadingDynamic] = useState(false)
  const [errorStatic, setErrorStatic] = useState<string | null>(null)
  const [errorDynamic, setErrorDynamic] = useState<string | null>(null)

  const fetchStatic = useCallback(async () => {
    if (!active) return
    setLoadingStatic(true)
    try {
      const data = await api.getStaticInfo()
      setStaticInfo(data)
      setErrorStatic(null)
    } catch (e: unknown) {
      setErrorStatic(e instanceof Error ? e.message : 'Failed to fetch static info')
    } finally {
      setLoadingStatic(false)
    }
  }, [active])

  const refreshStatic = useCallback(async () => {
    if (!active) return
    setLoadingStatic(true)
    try {
      const data = await api.refreshStaticInfo()
      setStaticInfo(data)
      setErrorStatic(null)
    } catch (e: unknown) {
      setErrorStatic(e instanceof Error ? e.message : 'Failed to refresh info')
    } finally {
      setLoadingStatic(false)
    }
  }, [active])

  const fetchDynamic = useCallback(async () => {
    if (!active) return
    setLoadingDynamic(true)
    try {
      const data = await api.getDynamicInfo()
      setDynamicInfo(data)
      setErrorDynamic(null)
    } catch (e: unknown) {
      setErrorDynamic(e instanceof Error ? e.message : 'Failed to fetch dynamic info')
    } finally {
      setLoadingDynamic(false)
    }
  }, [active])

  useEffect(() => {
    fetchStatic()
  }, [fetchStatic])

  usePolling(fetchDynamic, 3000, active)

  const staticSections = useMemo(() => {
    if (!staticInfo) return []

    const gpuFreq = formatMap(staticInfo.gpu.freq_bounds_mhz, (val, key) =>
      `${key}: ${val.min_mhz ?? 'N/A'} / ${val.max_mhz ?? 'N/A'} MHz`
    )
    const gpuVram = formatMap(staticInfo.gpu.vram, (val, key) =>
      `${key}: ${val.usage_percent ?? 'N/A'}%`
    )
    const gpuPcie = formatMap(staticInfo.gpu.pcie, (val, key) =>
      `${key}: ${val.current_speed ?? 'N/A'} x${val.current_width ?? 'N/A'}`
    )

    const npuFreq = formatMap(staticInfo.npu.freq_bounds_mhz, (val, key) =>
      `${key}: ${val.min_mhz ?? 'N/A'} / ${val.max_mhz ?? 'N/A'} MHz`
    )

    const diskSizes = staticInfo.disk.devices.length
      ? staticInfo.disk.devices
        .map((disk) => `${disk.name}: ${disk.size_gb ?? 'N/A'} GB`)
        .join(' | ')
      : 'N/A'

    return [
      {
        title: 'BIOS & OS',
        items: [
          { label: 'BIOS Version', value: staticInfo.bios.version },
          { label: 'OS Version', value: staticInfo.os.version },
          { label: 'Kernel Cmdline', value: staticInfo.driver.kernel_cmdline },
        ],
      },
      {
        title: 'Driver Versions',
        items: [
          { label: 'Mesa', value: formatPackage(staticInfo.driver.mesa) },
          { label: 'OpenCL', value: formatPackage(staticInfo.driver.opencl) },
          { label: 'Level Zero', value: formatPackage(staticInfo.driver.level_zero) },
          { label: 'Media', value: formatPackage(staticInfo.driver.media) },
          { label: 'NPU FW', value: staticInfo.driver.npu_fw },
        ],
      },
      {
        title: 'CPU',
        items: [
          { label: 'Model', value: staticInfo.cpu.model_name },
          { label: 'Logical Cores', value: staticInfo.cpu.core_count.logical },
          { label: 'Physical Cores', value: staticInfo.cpu.core_count.physical },
          { label: 'Min/Max Freq', value: `${staticInfo.cpu.freq_mhz.min_mhz ?? 'N/A'} / ${staticInfo.cpu.freq_mhz.max_mhz ?? 'N/A'} MHz` },
        ],
      },
      {
        title: 'Memory',
        items: [
          { label: 'DDR Speeds', value: staticInfo.memory.ddr_speeds },
          { label: 'Total', value: `${staticInfo.memory.total_gb ?? 'N/A'} GB` },
        ],
      },
      {
        title: 'Network',
        items: [
          { label: 'NIC Count', value: staticInfo.io.nic_count },
          { label: 'Primary Interface', value: staticInfo.io.primary_interface },
          { label: 'Network Peak', value: `${staticInfo.io.network_peak_mbps ?? 'N/A'} Mbps` },
          {
            label: 'Interface Speeds',
            value: formatMap(staticInfo.io.network_speeds_mbps, (val, key) => `${key}: ${val} Mbps`),
          },
        ],
      },
      {
        title: 'Disk',
        items: [
          { label: 'Disk Count', value: staticInfo.disk.device_count },
          { label: 'Total Size', value: `${staticInfo.disk.total_size_gb ?? 'N/A'} GB` },
          { label: 'Device Sizes', value: diskSizes },
        ],
      },
      {
        title: 'GPU',
        items: [
          { label: 'GPU Count', value: staticInfo.gpu.count },
          { label: 'Names', value: staticInfo.gpu.names },
          {
            label: 'Engines',
            value: formatMap(staticInfo.gpu.engines, (val, key) => `${key}: ${val.join(', ')}`),
          },
          { label: 'Min/Max Freq', value: gpuFreq },
          { label: 'VRAM Usage', value: gpuVram },
          { label: 'PCIE Link', value: gpuPcie },
        ],
      },
      {
        title: 'NPU',
        items: [
          { label: 'Names', value: staticInfo.npu.names },
          { label: 'Min/Max Freq', value: npuFreq },
        ],
      },
    ]
  }, [staticInfo])

  const primaryInterface = staticInfo?.io.primary_interface
  const dynamicNetwork = dynamicInfo?.network
  const primaryNet = primaryInterface && dynamicNetwork?.interfaces[primaryInterface]

  const diskEntries = dynamicInfo?.disk?.disk_io ? Object.entries(dynamicInfo.disk.disk_io) : []
  const topDisk = diskEntries[0]?.[1]
  const qmassa = dynamicInfo?.gpu.qmassa
  const qDevice = qmassa?.parsed?.devices?.[0]
  const qFreqs = qDevice?.freqs ?? []
  const gt0 = qFreqs.find((f) => f.name === 'gt0') ?? qFreqs[0]
  const gt1 = qFreqs.find((f) => f.name === 'gt1') ?? qFreqs[1]
  const engineUtil = qDevice?.engine_util ?? {}

  return (
    <div style={{ padding: '16px 0' }}>
      <Title level={5} style={{ color: COLORS.text, marginBottom: 12 }}>
        <Space size={10}>
          Configuration
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={refreshStatic}
            loading={loadingStatic}
          >
            Refresh
          </Button>
        </Space>
      </Title>

      {errorStatic && (
        <Alert message="Static Info Error" description={errorStatic} type="error" showIcon style={{ marginBottom: 12 }} />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {loadingStatic && !staticInfo ? (
          <Col span={24}>
            <Card style={{ background: COLORS.panelBg, border: `1px solid ${COLORS.border}`, borderRadius: 6 }}>
              <Spin size="small" />
            </Card>
          </Col>
        ) : (
          staticSections.map((section) => (
            <Col key={section.title} xs={24} md={12} lg={8}>
              <PanelCard title={section.title} icon={<SettingOutlined />}
              >
                <Descriptions
                  size="small"
                  column={1}
                  contentStyle={{ color: COLORS.text, fontSize: 12 }}
                  labelStyle={{ color: COLORS.textMuted, fontSize: 12 }}
                >
                  {section.items.map((item) => (
                    <Descriptions.Item key={item.label} label={item.label}>
                      {formatValue(item.value)}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </PanelCard>
            </Col>
          ))
        )}
      </Row>

      <Title level={5} style={{ color: COLORS.text, marginBottom: 12 }}>
        Dynamic Telemetry
      </Title>

      {errorDynamic && (
        <Alert message="Dynamic Info Error" description={errorDynamic} type="error" showIcon style={{ marginBottom: 12 }} />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} lg={8}>
          <PanelCard title="CPU" icon={<ThunderboltOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow label="Total Usage" value={dynamicInfo?.cpu.usage_total ?? 'N/A'} unit="%" />
                <MetricRow label="P-Core Usage" value={dynamicInfo?.cpu.p_core_usage ?? 'N/A'} unit="%" />
                <MetricRow label="E-Core Usage" value={dynamicInfo?.cpu.e_core_usage ?? 'N/A'} unit="%" />
                <MetricRow label="P-Core Freq" value={dynamicInfo?.cpu.p_core_freq_mhz ?? 'N/A'} unit="MHz" />
                <MetricRow label="E-Core Freq" value={dynamicInfo?.cpu.e_core_freq_mhz ?? 'N/A'} unit="MHz" />
                <MetricRow label="Core Type Source" value={dynamicInfo?.cpu.core_type_source ?? 'N/A'} />
              </>
            )}
          </PanelCard>
        </Col>

        <Col xs={24} md={12} lg={8}>
          <PanelCard title="Memory & Pressure" icon={<DatabaseOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow label="Memory Usage" value={dynamicInfo?.memory.usage_percent ?? 'N/A'} unit="%" />
                <MetricRow label="CPU Pressure" value={dynamicInfo?.pressure?.cpu ?? 'N/A'} />
                <MetricRow label="Memory Pressure" value={dynamicInfo?.pressure?.memory ?? 'N/A'} />
                <MetricRow label="IO Pressure" value={dynamicInfo?.pressure?.io ?? 'N/A'} />
              </>
            )}
          </PanelCard>
        </Col>

        <Col xs={24} md={12} lg={8}>
          <PanelCard title="Network" icon={<WifiOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow
                  label="Primary Interface"
                  value={primaryInterface || 'N/A'}
                />
                <MetricRow
                  label="RX Rate"
                  value={primaryNet?.rx_bytes_per_sec ?? dynamicNetwork?.total.rx_bytes_per_sec ?? 'N/A'}
                  unit="B/s"
                />
                <MetricRow
                  label="TX Rate"
                  value={primaryNet?.tx_bytes_per_sec ?? dynamicNetwork?.total.tx_bytes_per_sec ?? 'N/A'}
                  unit="B/s"
                />
              </>
            )}
          </PanelCard>
        </Col>

        <Col xs={24} md={12} lg={8}>
          <PanelCard title="Disk" icon={<DatabaseOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow label="Top Disk" value={diskEntries[0]?.[0] ?? 'N/A'} />
                <MetricRow label="Read" value={topDisk?.read_kb_per_sec ?? 'N/A'} unit="KB/s" />
                <MetricRow label="Write" value={topDisk?.write_kb_per_sec ?? 'N/A'} unit="KB/s" />
                <MetricRow label="Utilization" value={topDisk?.utilization ?? 'N/A'} unit="%" />
              </>
            )}
          </PanelCard>
        </Col>

        <Col xs={24} md={12} lg={8}>
          <PanelCard title="GPU" icon={<RobotOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow
                  label="VRAM Usage"
                  value={formatMap(dynamicInfo?.gpu.vram, (val, key) => `${key}: ${val.usage_percent ?? 'N/A'}%`)}
                />
                <MetricRow
                  label="Qmassa"
                  value={qmassa?.available ? 'Available' : 'Unavailable'}
                />
                <MetricRow
                  label="GPU Power"
                  value={qDevice?.power_w?.gpu ?? 'N/A'}
                  unit="W"
                />
                <MetricRow
                  label="Package Power"
                  value={qDevice?.power_w?.pkg ?? 'N/A'}
                  unit="W"
                />
                <MetricRow
                  label="GT0 Freq"
                  value={formatWithMax(gt0?.act_mhz ?? gt0?.cur_mhz ?? null, gt0?.max_mhz ?? null, 'MHz')}
                />
                <MetricRow
                  label="GT1 Freq"
                  value={formatWithMax(gt1?.act_mhz ?? gt1?.cur_mhz ?? null, gt1?.max_mhz ?? null, 'MHz')}
                />
                <MetricRow
                  label="GT0 Throttle"
                  value={formatThrottle(gt0)}
                />
                <MetricRow
                  label="GT1 Throttle"
                  value={formatThrottle(gt1)}
                />
                <MetricRow label="RCS Util" value={formatPercent(engineUtil.rcs)} />
                <MetricRow label="VCS Util" value={formatPercent(engineUtil.vcs)} />
                <MetricRow label="VECS Util" value={formatPercent(engineUtil.vecs)} />
                <MetricRow label="CCS Util" value={formatPercent(engineUtil.ccs)} />
                <MetricRow label="BCS Util" value={formatPercent(engineUtil.bcs)} />
                {qmassa?.error && (
                  <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>
                    {qmassa.error}
                  </Text>
                )}
              </>
            )}
          </PanelCard>
        </Col>

        <Col xs={24} md={12} lg={8}>
          <PanelCard title="NPU" icon={<RobotOutlined />}>
            {loadingDynamic && !dynamicInfo ? (
              <Spin size="small" />
            ) : (
              <>
                <MetricRow
                  label="npu-smi"
                  value={dynamicInfo?.npu.npu_smi.available ? 'Available' : 'Unavailable'}
                />
                {dynamicInfo?.npu.npu_smi.error && (
                  <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>
                    {dynamicInfo.npu.npu_smi.error}
                  </Text>
                )}
              </>
            )}
          </PanelCard>
        </Col>
      </Row>
    </div>
  )
}
