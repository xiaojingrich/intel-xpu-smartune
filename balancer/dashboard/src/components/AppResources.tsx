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
import type { AppInfo, Consumer } from '../api/types'
import { usePolling } from '../hooks/usePolling'

const { Text, Title } = Typography

interface AppRow {
  key: string
  app_id: string
  app_name: string
  cpu_usage: number
  memory_mb: number
  io_read_rate: number
  score: number
}

interface Props {
  active: boolean
}

function formatBytes(kb: number): string {
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`
  return `${(kb / 1024).toFixed(1)} MB/s`
}

export default function AppResources({ active }: Props) {
  const [rows, setRows] = useState<AppRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [reachThreshold, setReachThreshold] = useState(false)

  const fetchData = useCallback(async () => {
    try {
      const data = await api.getTopConsumers()
      setReachThreshold(data.reach_threshold)

      const appMap = new Map<string, AppRow>()
      data.consumers.forEach((c: Consumer) => {
        const app = c.app
        if (!app?.app_id) return
        const existing = appMap.get(app.app_id)
        if (!existing) {
          appMap.set(app.app_id, {
            key: app.app_id,
            app_id: app.app_id,
            app_name: app.app_name ?? 'Unknown',
            cpu_usage: app.cpu_usage ?? 0,
            memory_mb: app.memory_mb ?? 0,
            io_read_rate: app.io_read_rate ?? 0,
            score: app.score ?? 0,
          })
        }
      })

      setRows(Array.from(appMap.values()).sort((a, b) => b.score - a.score))
      setError(null)
      setLastUpdated(new Date())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch data')
    } finally {
      setLoading(false)
    }
  }, [])

  usePolling(fetchData, 5000, active)

  const columns: ColumnsType<AppRow> = [
    {
      title: 'App Name',
      dataIndex: 'app_name',
      key: 'app_name',
      render: (name: string) => (
        <Text style={{ color: COLORS.accent, fontWeight: 500 }}>{name}</Text>
      ),
      sorter: (a, b) => a.app_name.localeCompare(b.app_name),
    },
    {
      title: 'CPU %',
      dataIndex: 'cpu_usage',
      key: 'cpu_usage',
      width: 130,
      sorter: (a, b) => a.cpu_usage - b.cpu_usage,
      render: (v: number) => {
        const pct = v * 100
        const color = pct > 80 ? COLORS.red : pct > 50 ? COLORS.orange : COLORS.green
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Text style={{ color, fontSize: 12 }}>{pct.toFixed(1)}%</Text>
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
      title: 'Memory (MB)',
      dataIndex: 'memory_mb',
      key: 'memory_mb',
      width: 120,
      sorter: (a, b) => a.memory_mb - b.memory_mb,
      render: (v: number) => (
        <Text style={{ color: COLORS.text }}>{v.toFixed(1)}</Text>
      ),
    },
    {
      title: 'IO Read Rate',
      dataIndex: 'io_read_rate',
      key: 'io_read_rate',
      width: 130,
      sorter: (a, b) => a.io_read_rate - b.io_read_rate,
      render: (v: number) => (
        <Text style={{ color: COLORS.textMuted }}>{formatBytes(v)}</Text>
      ),
    },
    {
      title: 'Score',
      dataIndex: 'score',
      key: 'score',
      width: 100,
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Title level={5} style={{ color: COLORS.text, margin: 0 }}>
              App Resource Usage
            </Title>
            {reachThreshold && (
              <Tag color="error">⚠ Threshold Reached</Tag>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {lastUpdated && (
              <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>
                <ReloadOutlined style={{ marginRight: 4 }} />
                Updated: {lastUpdated.toLocaleTimeString()}
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
          pagination={{ pageSize: 20, showSizeChanger: true }}
          rowClassName={(_, idx) =>
            idx % 2 === 1 ? 'table-row-alt' : ''
          }
          style={{ color: COLORS.text }}
          locale={{
            emptyText: (
              <div style={{ padding: 40, color: COLORS.textMuted, textAlign: 'center' }}>
                {loading ? <Spin /> : 'No app consumers data available'}
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
