import React, { useState, useCallback } from 'react'
import { Row, Col, Card, Typography, Alert, Badge, Tag } from 'antd'
import { COLORS, getPressureColor, getPressureLabel } from '../styles/theme'
import { api } from '../api/client'
import type { PressureData, NetworkData } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import {
  RadialBarChart,
  RadialBar,
  ResponsiveContainer,
  PolarAngleAxis,
} from 'recharts'

const { Text, Title } = Typography

interface Props {
  active: boolean
}

interface GaugePanelProps {
  title: string
  value: number
  subtitle?: string
  description?: string
}

function GaugePanel({ title, value, subtitle, description }: GaugePanelProps) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(value, 1)) : 0
  const color = getPressureColor(safeValue)
  const pointerColor = COLORS.text
  const label = getPressureLabel(safeValue)
  const pct = Math.round(safeValue * 100)
  const needleAngle = 180 - (pct * 180) / 100

  const gaugeData = [{ value: pct, fill: color }]

  return (
    <Card
      style={{
        background: COLORS.panelBg,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 6,
        height: '100%',
      }}
      bodyStyle={{ padding: 20 }}
    >
      <div style={{ textAlign: 'center' }}>
        <Text style={{ color: COLORS.textMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 }}>
          {title}
        </Text>

        <div style={{ position: 'relative', height: 220, overflow: 'hidden' }}>
          <ResponsiveContainer width="100%" height="100%">
            <RadialBarChart
              cx="50%"
              cy="70%"
              innerRadius="60%"
              outerRadius="88%"
              startAngle={180}
              endAngle={0}
              data={gaugeData}
            >
              <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
              <RadialBar
                background={{ fill: COLORS.border }}
                dataKey="value"
                cornerRadius={4}
                fill={color}
              />
            </RadialBarChart>
          </ResponsiveContainer>

          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
            style={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              pointerEvents: 'none',
              filter: 'drop-shadow(0 0 4px rgba(90, 169, 255, 0.2))',
              zIndex: 1,
            }}
          >
            <line
              x1="50"
              y1="70"
              x2="73"
              y2="70"
              stroke={pointerColor}
              strokeWidth="2.2"
              strokeLinecap="round"
              transform={`rotate(${needleAngle} 50 70)`}
            />
            <circle cx="50" cy="70" r="2.8" fill={pointerColor} />
          </svg>

          <div
            style={{
              position: 'absolute',
              bottom: '18%',
              left: '50%',
              transform: 'translateX(-50%)',
              textAlign: 'center',
              zIndex: 2,
            }}
          >
            <div
              style={{
                fontSize: 36,
                fontWeight: 700,
                color,
                lineHeight: 1,
                fontFamily: 'monospace',
              }}
            >
              {pct}%
            </div>
          </div>
        </div>

        <div style={{ marginTop: 8 }}>
          <Tag
            style={{
              color,
              borderColor: color,
              background: `${color}20`,
              fontSize: 12,
              fontWeight: 700,
              padding: '2px 12px',
              letterSpacing: 1,
            }}
          >
            {label}
          </Tag>
        </div>

        {subtitle && (
          <Text style={{ color: COLORS.textMuted, fontSize: 11, display: 'block', marginTop: 8 }}>
            {subtitle}
          </Text>
        )}
        {description && (
          <Text style={{ color: COLORS.textMuted, fontSize: 10, display: 'block', marginTop: 4 }}>
            {description}
          </Text>
        )}
      </div>
    </Card>
  )
}

interface ThresholdBarProps {
  label: string
  value: number
}

function ThresholdBar({ label, value }: ThresholdBarProps) {
  const color = getPressureColor(value)
  const pct = Math.round(value * 100)

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>{label}</Text>
        <Text style={{ color, fontSize: 12, fontWeight: 600 }}>{pct}%</Text>
      </div>
      <div
        style={{
          height: 8,
          background: COLORS.border,
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}88 0%, ${color} 100%)`,
            borderRadius: 4,
            transition: 'width 0.5s ease',
          }}
        />
      </div>
    </div>
  )
}

export default function PressureDashboard({ active }: Props) {
  const [pressure, setPressure] = useState<PressureData | null>(null)
  const [network, setNetwork] = useState<NetworkData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [p, n] = await Promise.all([api.getPressure(), api.getNetwork()])
      setPressure(p)
      setNetwork(n)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch pressure data')
    } finally {
      setLoading(false)
    }
  }, [])

  usePolling(fetchData, 3000, active)

  const systemPressure = pressure ? (pressure.cpu + pressure.memory) / 2 : 0
  const diskPressure = pressure?.io ?? 0
  const networkPressure = network ? (network.rx + network.tx) / 2 : 0

  const legendItems = [
    { label: 'LOW', range: '0–30%', color: COLORS.green },
    { label: 'MEDIUM', range: '30–60%', color: COLORS.yellow },
    { label: 'HIGH', range: '60–80%', color: COLORS.orange },
    { label: 'CRITICAL', range: '80–100%', color: COLORS.red },
  ]

  return (
    <div style={{ padding: '16px 0' }}>
      {error && (
        <Alert
          message="API Error"
          description={error}
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={5} style={{ color: COLORS.text, margin: 0 }}>
          System Pressure Overview
        </Title>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          {legendItems.map((item) => (
            <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <div
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: item.color,
                }}
              />
              <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>
                {item.label} ({item.range})
              </Text>
            </div>
          ))}
          <Badge status="processing" color={COLORS.green} text={
            <Text style={{ color: COLORS.textMuted, fontSize: 11 }}>Auto-refresh 3s</Text>
          } />
        </div>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <GaugePanel
            title="System Pressure"
            value={systemPressure}
            subtitle={pressure ? `CPU: ${(pressure.cpu * 100).toFixed(0)}% | Mem: ${(pressure.memory * 100).toFixed(0)}%` : undefined}
            description="Combined CPU + Memory pressure"
          />
        </Col>
        <Col xs={24} md={8}>
          <GaugePanel
            title="Disk IO Pressure"
            value={diskPressure}
            subtitle={pressure ? `IO: ${(pressure.io * 100).toFixed(0)}%` : undefined}
            description="Disk I/O saturation"
          />
        </Col>
        <Col xs={24} md={8}>
          <GaugePanel
            title="Network IO Pressure"
            value={networkPressure}
            subtitle={network ? `RX: ${(network.rx * 100).toFixed(0)}% | TX: ${(network.tx * 100).toFixed(0)}%` : undefined}
            description="Network bandwidth utilization"
          />
        </Col>
      </Row>

      {/* Detailed breakdown */}
      {pressure && network && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} lg={12}>
            <Card
              title={<Text style={{ color: COLORS.text, fontSize: 13, fontWeight: 600 }}>Resource Breakdown</Text>}
              style={{ background: COLORS.panelBg, border: `1px solid ${COLORS.border}`, borderRadius: 6 }}
              headStyle={{ borderBottom: `1px solid ${COLORS.border}`, padding: '8px 16px' }}
              bodyStyle={{ padding: 20 }}
            >
              <ThresholdBar label="CPU Pressure" value={pressure.cpu} />
              <ThresholdBar label="Memory Pressure" value={pressure.memory} />
              <ThresholdBar label="IO Pressure" value={pressure.io} />
              <ThresholdBar label="Network RX" value={network.rx} />
              <ThresholdBar label="Network TX" value={network.tx} />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card
              title={<Text style={{ color: COLORS.text, fontSize: 13, fontWeight: 600 }}>Status Summary</Text>}
              style={{ background: COLORS.panelBg, border: `1px solid ${COLORS.border}`, borderRadius: 6 }}
              headStyle={{ borderBottom: `1px solid ${COLORS.border}`, padding: '8px 16px' }}
              bodyStyle={{ padding: 20 }}
            >
              {[
                { name: 'System Pressure', value: systemPressure },
                { name: 'CPU', value: pressure.cpu },
                { name: 'Memory', value: pressure.memory },
                { name: 'Disk IO', value: diskPressure },
                { name: 'Network RX', value: network.rx },
                { name: 'Network TX', value: network.tx },
              ].map(({ name, value }) => {
                const color = getPressureColor(value)
                const label = getPressureLabel(value)
                return (
                  <div
                    key={name}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '6px 0',
                      borderBottom: `1px solid ${COLORS.border}33`,
                    }}
                  >
                    <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>{name}</Text>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Text style={{ color, fontSize: 13, fontWeight: 600 }}>
                        {(value * 100).toFixed(1)}%
                      </Text>
                      <Tag
                        style={{
                          color,
                          borderColor: color,
                          background: `${color}15`,
                          fontSize: 10,
                          margin: 0,
                          padding: '0 6px',
                        }}
                      >
                        {label}
                      </Tag>
                    </div>
                  </div>
                )
              })}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  )
}
