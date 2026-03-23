import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Descriptions, Row, Space, Spin, Typography } from 'antd'
import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import type { PackageInfo, StaticInfoData } from '../api/types'
import { COLORS } from '../styles/theme'

const { Text, Title } = Typography

interface Props {
  active: boolean
}

function formatPackageText(pkg?: PackageInfo | null): string {
  if (!pkg?.installed) return 'Not Installed'
  return pkg.version || pkg.raw || 'Installed'
}

function formatPlain(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'N/A'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'N/A'
  return `${value}`
}

function formatCompactText(value: unknown, head = 24, tail = 12): string {
  const text = formatPlain(value)
  if (text === 'N/A') return text
  if (text.length <= head + tail + 3) return text
  return `${text.slice(0, head)}...${text.slice(-tail)}`
}

function formatFreqRange(min?: number | null, max?: number | null): string {
  if (typeof min === 'number' && typeof max === 'number') return `${Math.round(min)}-${Math.round(max)} MHz`
  if (typeof min === 'number') return `min ${Math.round(min)} MHz`
  if (typeof max === 'number') return `max ${Math.round(max)} MHz`
  return 'N/A'
}

function summarizeFreqBounds(bounds?: Record<string, { min_mhz: number | null; max_mhz: number | null }>): string {
  const entries = Object.entries(bounds || {})
  if (!entries.length) return 'N/A'
  return entries.map(([name, range]) => `${name}: ${formatFreqRange(range.min_mhz, range.max_mhz)}`).join(' | ')
}

/** Cards with a PCIe entry = dGPU; rest = iGPU */
function buildGpuCardLabels(
  cardKeys: string[],
  pcieKeys: Set<string>,
): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const key of cardKeys) {
    if (pcieKeys.has(key)) {
      labels[key] = `dGPU(${key})`
    } else {
      labels[key] = `iGPU(${key})`
    }
  }
  return labels
}

function summarizeGpuFreqBounds(
  bounds?: Record<string, { min_mhz: number | null; max_mhz: number | null }>,
  pcie?: Record<string, unknown>,
): string {
  const entries = Object.entries(bounds || {})
  if (!entries.length) return 'N/A'
  const labels = buildGpuCardLabels(entries.map(([k]) => k), new Set(Object.keys(pcie || {})))
  return entries.map(([name, range]) => `${labels[name] ?? name}: ${formatFreqRange(range.min_mhz, range.max_mhz)}`).join(' | ')
}

function summarizeGpuPcie(
  pcie?: Record<string, { current_speed: string | null; current_width: string | null; max_speed: string | null; max_width: string | null }>,
  bounds?: Record<string, unknown>,
): string {
  const entries = Object.entries(pcie || {})
  if (!entries.length) return 'N/A'
  const allKeys = Object.keys(bounds || pcie || {})
  const labels = buildGpuCardLabels(allKeys, new Set(Object.keys(pcie || {})))
  return entries.map(([card, info]) => {
    const cur = info.current_speed && info.current_width
      ? `${info.current_speed} x${info.current_width}`
      : (info.current_speed || info.current_width || null)
    const max = info.max_speed && info.max_width ? `max ${info.max_speed} x${info.max_width}` : null
    const link = cur && max ? `${cur} (${max})` : (cur || max || 'N/A')
    return `${labels[card] ?? card}: ${link}`
  }).join(' | ')
}

function summarizeGpuPerDevice(
  staticInfo?: StaticInfoData | null,
): Array<{ label: string; engines: string; gtFreqs: string }> {
  if (!staticInfo) return []

  const cardKeySet = new Set<string>()
  Object.keys(staticInfo.gpu.engines || {}).forEach((k) => cardKeySet.add(k))
  Object.keys(staticInfo.gpu.freq_bounds_mhz || {}).forEach((k) => cardKeySet.add(k))
  Object.keys(staticInfo.gpu.pcie || {}).forEach((k) => cardKeySet.add(k))
  Object.keys(staticInfo.gpu.pci_addresses || {}).forEach((k) => cardKeySet.add(k))
  Object.keys(staticInfo.gpu.gt_freq_bounds_mhz || {}).forEach((k) => cardKeySet.add(k))

  const cardKeys = Array.from(cardKeySet).sort()
  const labels = buildGpuCardLabels(cardKeys, new Set(Object.keys(staticInfo.gpu.pcie || {})))

  return cardKeys.map((cardKey) => {
    const engines = staticInfo.gpu.engines?.[cardKey] || []
    const gt0 = staticInfo.gpu.gt_freq_bounds_mhz?.[cardKey]?.gt0
    const gt1 = staticInfo.gpu.gt_freq_bounds_mhz?.[cardKey]?.gt1
    const gtFreqs = [
      gt0 ? `gt0 ${formatFreqRange(gt0.min_mhz, gt0.max_mhz)}` : null,
      gt1 ? `gt1 ${formatFreqRange(gt1.min_mhz, gt1.max_mhz)}` : null,
    ].filter(Boolean).join(' | ')

    return {
      label: labels[cardKey] || cardKey,
      engines: engines.length ? engines.join(', ') : 'N/A',
      gtFreqs: gtFreqs || 'N/A',
    }
  })
}

function summarizeDiskSizes(devices?: Array<{ name: string; size_gb: number | null }>): string {
  if (!devices?.length) return 'N/A'
  return devices
    .map((disk) => `${disk.name}: ${typeof disk.size_gb === 'number' ? `${disk.size_gb.toFixed(2)} GB` : 'N/A'}`)
    .join(' | ')
}

function formatNetworkSpeed(value?: number | null): string {
  if (typeof value !== 'number' || value <= 0) return 'N/A'
  return `${Math.round(value)} Mbps`
}

function summarizeNetworkSpeeds(speeds?: Record<string, number>): string {
  const entries = Object.entries(speeds || {})
  if (!entries.length) return 'N/A'
  return entries.map(([name, speed]) => `${name}: ${formatNetworkSpeed(speed)}`).join(' | ')
}

function summarizeGpuNamesByCard(staticInfo?: StaticInfoData | null): string {
  if (!staticInfo) return 'N/A'

  const names = staticInfo.gpu.names || []
  const pciAddresses = staticInfo.gpu.pci_addresses || {}
  const cardKeys = Object.keys(pciAddresses).sort()
  if (!cardKeys.length) return formatPlain(names)

  const nameByBdf: Record<string, string> = {}
  names.forEach((line) => {
    const match = `${line}`.match(/^([0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])/i)
    if (match) {
      nameByBdf[match[1].toLowerCase()] = line
    }
  })

  const mapped = cardKeys.map((cardKey) => {
    const pci = pciAddresses[cardKey]
    const shortBdf = typeof pci === 'string' ? pci.replace(/^[0-9a-f]{4}:/i, '').toLowerCase() : ''
    const line = nameByBdf[shortBdf]
    return `${cardKey} -> ${line || 'N/A'}`
  })

  return mapped.join(' | ')
}

function SectionCard({
  title,
  items,
}: {
  title: string
  items: Array<{ label: string; value: React.ReactNode }>
}) {
  return (
    <Card
      className="perf-card perf-rise"
      bodyStyle={{ padding: 14 }}
      style={{ height: '100%' }}
    >
      <Text style={{ color: COLORS.text, fontSize: 13, fontWeight: 600 }}>{title}</Text>
      <Descriptions
        size="small"
        column={1}
        style={{ marginTop: 8 }}
        labelStyle={{ color: COLORS.textMuted, fontSize: 12, width: 140 }}
        contentStyle={{ color: COLORS.text, fontSize: 12, wordBreak: 'break-word' }}
      >
        {items.map((item) => (
          <Descriptions.Item key={`${title}-${item.label}`} label={item.label}>
            {item.value}
          </Descriptions.Item>
        ))}
      </Descriptions>
    </Card>
  )
}

export default function About({ active }: Props) {
  const [staticInfo, setStaticInfo] = useState<StaticInfoData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatic = useCallback(async (forceRefresh: boolean) => {
    if (!active) return
    setLoading(true)
    try {
      const data = forceRefresh ? await api.refreshStaticInfo() : await api.getStaticInfo()
      setStaticInfo(data)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch static info')
    } finally {
      setLoading(false)
    }
  }, [active])

  useEffect(() => {
    if (!active) return
    fetchStatic(false)
  }, [active, fetchStatic])

  const softwareSections = useMemo(() => {
    if (!staticInfo) return [] as Array<{ title: string; items: Array<{ label: string; value: string }> }>

    return [
      {
        title: 'Software & Drivers',
        items: [
          { label: 'OS version', value: formatPlain(staticInfo.os.version) },
          { label: 'BIOS version', value: formatPlain(staticInfo.bios.version) },
          { label: 'Kernel version', value: formatPlain(staticInfo.driver.kernel_version) },
          { label: 'GuC FW', value: formatPlain(staticInfo.driver.guc_fw) },
          { label: 'HuC FW', value: formatPlain(staticInfo.driver.huc_fw) },
          { label: 'NPU FW', value: formatCompactText(staticInfo.driver.npu_fw) },
          { label: 'Mesa', value: formatPackageText(staticInfo.driver.mesa) },
          { label: 'OpenCL', value: formatPackageText(staticInfo.driver.opencl) },
          { label: 'Level Zero', value: formatPackageText(staticInfo.driver.level_zero) },
          { label: 'Media', value: formatPackageText(staticInfo.driver.media) },
        ],
      },
    ]
  }, [staticInfo])

  const hardwareSections = useMemo(() => {
    if (!staticInfo) return [] as Array<{ title: string; items: Array<{ label: string; value: string }> }>

    return [
      {
        title: 'CPU & Memory',
        items: [
          { label: 'CPU model', value: formatPlain(staticInfo.cpu.model_name) },
          { label: 'Logical cores', value: formatPlain(staticInfo.cpu.core_count.logical) },
          { label: 'Physical cores', value: formatPlain(staticInfo.cpu.core_count.physical) },
          ...(staticInfo.cpu.freq_mhz.p_core_freq_mhz
            ? [{ label: 'P-Core freq range', value: formatFreqRange(staticInfo.cpu.freq_mhz.p_core_freq_mhz.min_mhz, staticInfo.cpu.freq_mhz.p_core_freq_mhz.max_mhz) }]
            : [{ label: 'CPU freq range', value: formatFreqRange(staticInfo.cpu.freq_mhz.min_mhz, staticInfo.cpu.freq_mhz.max_mhz) }]),
          ...(staticInfo.cpu.freq_mhz.e_core_freq_mhz
            ? [{ label: 'E-Core freq range', value: formatFreqRange(staticInfo.cpu.freq_mhz.e_core_freq_mhz.min_mhz, staticInfo.cpu.freq_mhz.e_core_freq_mhz.max_mhz) }]
            : []),
          { label: 'Memory total', value: typeof staticInfo.memory.total_gb === 'number' ? `${staticInfo.memory.total_gb.toFixed(1)} GB` : 'N/A' },
          { label: 'DDR speeds', value: formatPlain(staticInfo.memory.ddr_speeds) },
        ],
      },
      {
        title: 'IO & Disk',
        items: [
          { label: 'NIC count', value: formatPlain(staticInfo.io.nic_count) },
          { label: 'Primary NIC', value: formatPlain(staticInfo.io.primary_interface) },
          { label: 'Network peak', value: formatNetworkSpeed(staticInfo.io.network_peak_mbps) },
          { label: 'NIC speeds', value: summarizeNetworkSpeeds(staticInfo.io.network_speeds_mbps) },
          { label: 'Disk total size', value: typeof staticInfo.disk.total_size_gb === 'number' ? `${staticInfo.disk.total_size_gb.toFixed(2)} GB` : 'N/A' },
          { label: 'Disk devices', value: summarizeDiskSizes(staticInfo.disk.devices) },
        ],
      },
      {
        title: 'GPU & NPU',
        items: [
          { label: 'GPU count', value: formatPlain(staticInfo.gpu.count) },
          { label: 'GPU names', value: summarizeGpuNamesByCard(staticInfo) },
          { label: 'GPU PCIe', value: summarizeGpuPcie(staticInfo.gpu.pcie, staticInfo.gpu.freq_bounds_mhz) },
          ...summarizeGpuPerDevice(staticInfo).flatMap((gpu) => [
            { label: `${gpu.label} engines`, value: gpu.engines },
            { label: `${gpu.label} gt0/gt1 freq`, value: gpu.gtFreqs },
          ]),
          {
            label: 'NPU detail',
            value: (
              <span style={{ whiteSpace: 'pre-line' }}>
                {`NPU\n${formatPlain(staticInfo.npu.names)}`}
              </span>
            ),
          },
          { label: 'NPU Device', value: formatPlain(staticInfo.npu.pciid) },
          { label: 'NPU Driver', value: formatPlain(staticInfo.npu.driver_version) },
          { label: 'NPU freq bounds', value: summarizeFreqBounds(staticInfo.npu.freq_bounds_mhz) },
        ],
      },
    ]
  }, [staticInfo])

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Space size={8}>
          <InfoCircleOutlined style={{ color: COLORS.accent }} />
          <Title level={4} style={{ color: COLORS.text, margin: 0 }}>
            About
          </Title>
          {staticInfo?.collected_at && (
            <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>
              Updated: {staticInfo.collected_at}
            </Text>
          )}
        </Space>

        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => fetchStatic(true)}
          loading={loading}
        >
          Refresh
        </Button>
      </div>

      {error && (
        <Alert
          message="About Data Error"
          description={error}
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
        />
      )}

      {loading && !staticInfo ? (
        <Card className="perf-card" bodyStyle={{ padding: 18 }}>
          <Spin size="small" />
        </Card>
      ) : (
        <>
          <Text style={{ color: COLORS.textMuted, fontSize: 12, display: 'block', marginBottom: 8 }}>
            Software Configuration
          </Text>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            {softwareSections.map((section) => (
              <Col key={`soft-${section.title}`} xs={24} md={12}>
                <SectionCard title={section.title} items={section.items} />
              </Col>
            ))}
          </Row>

          <Text style={{ color: COLORS.textMuted, fontSize: 12, display: 'block', marginBottom: 8 }}>
            Hardware Configuration
          </Text>
          <Row gutter={[16, 16]}>
            {hardwareSections.map((section) => (
              <Col key={`hw-${section.title}`} xs={24} lg={8}>
                <SectionCard title={section.title} items={section.items} />
              </Col>
            ))}
          </Row>
        </>
      )}
    </div>
  )
}
