import React, { useState, useCallback } from 'react'
import {
  Table,
  Tag,
  Typography,
  Alert,
  Spin,
  Badge,
  Progress,
  Space,
  Tooltip,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { COLORS } from '../styles/theme'
import { api } from '../api/client'
import type { Consumer } from '../api/types'
import { usePolling } from '../hooks/usePolling'

const { Text, Title } = Typography

interface ProcessRow {
  key: string
  pid: number
  name: string
  cmdline: string
  cpu_avg: number
  memory_rss: number
  io_read_rate: number
  score: number
  app_name: string
}

interface Props {
  active: boolean
}

function formatBytes(kb: number): string {
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`
  return `${(kb / 1024).toFixed(1)} MB/s`
}

function formatMemory(kb: number): string {
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  if (kb < 1024 * 1024) return `${(kb / 1024).toFixed(1)} MB`
  return `${(kb / 1024 / 1024).toFixed(2)} GB`
}

export default function ProcessResources({ active }: Props) {
  const [rows, setRows] = useState<ProcessRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const data = await api.getTopConsumers()
      const processRows: ProcessRow[] = data.consumers.map((c: Consumer, idx: number) => {
        const p = c.process
        const app = c.app
        return {
          key: `${p?.pid ?? idx}-${idx}`,
          pid: p?.pid ?? 0,
          name: p?.name ?? 'Unknown',
          cmdline: p?.cmdline ?? '',
          cpu_avg: p?.cpu_avg ?? 0,
          memory_rss: p?.memory_rss ?? 0,
          io_read_rate: p?.io_read_rate ?? 0,
          score: p?.score ?? app?.score ?? 0,
          app_name: app?.app_name ?? '',
        }
      })
      setRows(processRows.sort((a, b) => b.score - a.score))
      setError(null)
      setLastUpdated(new Date())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch data')
    } finally {
      setLoading(false)
    }
  }, [])

  usePolling(fetchData, 5000, active)

  const columns: ColumnsType<ProcessRow> = [
    {
      title: 'PID',
      dataIndex: 'pid',
      key: 'pid',
      width: 70,
      sorter: (a, b) => a.pid - b.pid,
      render: (v: number) => (
        <Text style={{ color: COLORS.textMuted, fontFamily: 'monospace', fontSize: 11 }}>
          {v || '—'}
        </Text>
      ),
    },
    {
      title: 'Process Name',
      dataIndex: 'name',
      key: 'name',
      width: 150,
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (v: string) => (
        <Text style={{ color: COLORS.accent, fontWeight: 500, fontSize: 12 }}>{v}</Text>
      ),
    },
    {
      title: 'App',
      dataIndex: 'app_name',
      key: 'app_name',
      width: 130,
      render: (v: string) =>
        v ? <Tag style={{ fontSize: 11 }} color="blue">{v}</Tag> : <Text style={{ color: COLORS.textMuted }}>—</Text>,
    },
    {
      title: 'Command',
      dataIndex: 'cmdline',
      key: 'cmdline',
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v} overlayStyle={{ maxWidth: 500 }}>
          <Text
            style={{
              color: COLORS.textMuted,
              fontFamily: 'monospace',
              fontSize: 11,
              maxWidth: 240,
              display: 'inline-block',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {v || '—'}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: 'CPU Avg %',
      dataIndex: 'cpu_avg',
      key: 'cpu_avg',
      width: 120,
      sorter: (a, b) => a.cpu_avg - b.cpu_avg,
      render: (v: number) => {
        const pct = v * 100
        const color = pct > 80 ? COLORS.red : pct > 50 ? COLORS.orange : COLORS.green
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Text style={{ color, fontSize: 11 }}>{pct.toFixed(1)}%</Text>
            <Progress
              percent={Math.min(pct, 100)}
              showInfo={false}
              strokeColor={color}
              trailColor={COLORS.border}
              size="small"
            />
          </Space>
        )
      },
    },
    {
      title: 'Memory RSS',
      dataIndex: 'memory_rss',
      key: 'memory_rss',
      width: 110,
      sorter: (a, b) => a.memory_rss - b.memory_rss,
      render: (v: number) => (
        <Text style={{ color: COLORS.text, fontSize: 12 }}>{formatMemory(v)}</Text>
      ),
    },
    {
      title: 'IO Read Rate',
      dataIndex: 'io_read_rate',
      key: 'io_read_rate',
      width: 120,
      sorter: (a, b) => a.io_read_rate - b.io_read_rate,
      render: (v: number) => (
        <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>{formatBytes(v)}</Text>
      ),
    },
    {
      title: 'Score',
      dataIndex: 'score',
      key: 'score',
      width: 90,
      defaultSortOrder: 'descend',
      sorter: (a, b) => a.score - b.score,
      render: (v: number) => {
        const color = v > 80 ? COLORS.red : v > 50 ? COLORS.orange : v > 20 ? COLORS.yellow : COLORS.green
        return (
          <Tag style={{ color, borderColor: color, background: `${color}15`, fontSize: 12, fontWeight: 600 }}>
            {v.toFixed(1)}
          </Tag>
        )
      },
    },
  ]

  return (
    <div style={{ padding: '16px 0' }}>
      <div
        style={{
          background: COLORS.panelBg,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 6,
          padding: 16,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
          }}
        >
          <Title level={5} style={{ color: COLORS.text, margin: 0 }}>
            Process Resource Usage
          </Title>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {lastUpdated && (
              <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>
                <ReloadOutlined style={{ marginRight: 4 }} />
                {lastUpdated.toLocaleTimeString()}
              </Text>
            )}
            <Badge
              status="processing"
              color={COLORS.green}
              text={<Text style={{ color: COLORS.textMuted, fontSize: 11 }}>Auto-refresh 5s</Text>}
            />
          </div>
        </div>

        {error && (
          <Alert
            message="API Error"
            description={error}
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
          />
        )}

        <Table
          columns={columns}
          dataSource={rows}
          loading={loading}
          size="small"
          scroll={{ x: 900 }}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          rowClassName={(_, idx) => (idx % 2 === 1 ? 'table-row-alt' : '')}
          locale={{
            emptyText: (
              <div style={{ padding: 40, color: COLORS.textMuted, textAlign: 'center' }}>
                {loading ? <Spin /> : 'No process data available'}
              </div>
            ),
          }}
        />

        <style>{`
          .table-row-alt td { background: ${COLORS.rowAlt} !important; }
          .ant-table { background: transparent !important; }
          .ant-table-thead > tr > th {
            background: ${COLORS.headerBg} !important;
            color: ${COLORS.textMuted} !important;
            font-size: 11px !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid ${COLORS.border} !important;
          }
          .ant-table-tbody > tr > td {
            border-bottom: 1px solid ${COLORS.border}55 !important;
          }
          .ant-table-tbody > tr:hover > td {
            background: ${COLORS.rowAlt} !important;
          }
        `}</style>
      </div>
    </div>
  )
}
