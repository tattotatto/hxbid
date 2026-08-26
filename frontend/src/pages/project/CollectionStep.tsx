import { useEffect, useState, useCallback } from 'react'
import { Card, Tag, Button, Space, message, Progress, Alert, List, Popconfirm } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  UploadOutlined,
  UserAddOutlined,
  LinkOutlined,
  PlusOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import QualificationPickerModal from './QualificationPickerModal'
import QuickPersonnelForm from './QuickPersonnelForm'
import QuickQualificationUpload from './QuickQualificationUpload'

interface RequirementItem {
  name: string
  category: string
  details?: string
}

interface ResourceMatch {
  requirement: RequirementItem
  matched: boolean
  matches: any[]
  match_status: string
}

interface CollectionData {
  project_id: string
  status: string
  document_items: ResourceMatch[]
  personnel_items: ResourceMatch[]
  is_complete: boolean
}

interface Props {
  projectId: string
  onComplete: () => void
}

export default function CollectionStep({ projectId, onComplete }: Props) {
  const [data, setData] = useState<CollectionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)

  // Modal state
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerReq, setPickerReq] = useState('')
  const [pickerDefaultMode, setPickerDefaultMode] = useState<'qualification' | 'personnel' | 'contract' | 'history_bid' | 'company'>('qualification')
  const [quickPersonnelOpen, setQuickPersonnelOpen] = useState(false)
  const [quickPersonnelRole, setQuickPersonnelRole] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadReq, setUploadReq] = useState('')
  const [company, setCompany] = useState<any>(null)

  const fetchStatus = useCallback(async () => {
    setLoading(true)
    try {
      const res = await client.get(`/collection/${projectId}/status`)
      setData(res.data)
    } catch {
      message.error('获取搜集状态失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  useEffect(() => {
    client.get('/company/').then((r) => setCompany(r.data)).catch(() => {})
  }, [])

  // ── Actions ──

  const handleLinkQuals = async (quals: any[], reqName: string) => {
    try {
      for (const q of quals) {
        await client.post(`/collection/${projectId}/qualification/link`, {
          qualification_id: q.id,
          requirement_name: reqName,
        })
      }
      message.success(`已关联 ${quals.length} 项资质`)
      setPickerOpen(false)
      fetchStatus()
    } catch {
      message.error('关联失败')
    }
  }

  // Batch link contracts
  const handleLinkContracts = async (contracts: any[], reqName: string) => {
    try {
      for (const c of contracts) {
        await client.post(`/collection/${projectId}/contract/link`, {
          contract_id: c.id,
          requirement_name: reqName,
        })
      }
      message.success(`已关联 ${contracts.length} 份合同`)
      setPickerOpen(false)
      fetchStatus()
    } catch {
      message.error('关联失败')
    }
  }

  // Batch assign personnel
  const handleAssignPersonnelList = async (personnelList: any[], role: string) => {
    try {
      for (const p of personnelList) {
        await client.post(`/collection/${projectId}/personnel/assign`, {
          personnel_id: p.id,
          role,
          requirement_desc: role,
        })
      }
      message.success(`已分配 ${personnelList.length} 人`)
      setPickerOpen(false)
      setQuickPersonnelOpen(false)
      fetchStatus()
    } catch {
      message.error('分配失败')
    }
  }

  const handleConfirm = async () => {
    setConfirming(true)
    try {
      await client.post(`/collection/${projectId}/confirm`)
      message.success('信息搜集完成，可以开始生成标书')
      onComplete()
    } catch {
      message.error('确认失败')
    } finally {
      setConfirming(false)
    }
  }

  const handleSkip = async () => {
    setConfirming(true)
    try {
      await client.post(`/collection/${projectId}/confirm`)
      message.info('已跳过信息搜集')
      onComplete()
    } catch {
      message.error('操作失败')
    } finally {
      setConfirming(false)
    }
  }

  // 移除某条匹配（人员走 unassign，合同/资质走 unlink）
  const removeMatch = async (item: ResourceMatch, m: any) => {
    try {
      if (item.requirement.category === 'personnel') {
        await client.post(`/collection/${projectId}/personnel/unassign`, { assignment_id: m.link_id })
      } else if (item.requirement.category === 'contract_performance') {
        await client.post(`/collection/${projectId}/contract/unlink`, { requirement_name: item.requirement.name, resource_id: m.id })
      } else {
        await client.post(`/collection/${projectId}/qualification/unlink`, { requirement_name: item.requirement.name, resource_id: m.id })
      }
      message.success('已移除')
      fetchStatus()
    } catch {
      message.error('移除失败')
    }
  }

  // 三态状态 → 标签文案/颜色/图标
  const statusMeta = (s: string) => {
    if (s === 'selected') return { text: '已选择', color: 'green', icon: <CheckCircleOutlined /> }
    if (s === 'uploaded') return { text: '已上传', color: 'green', icon: <CheckCircleOutlined /> }
    if (s === 'auto') return { text: '自动匹配', color: 'blue', icon: <LinkOutlined /> }
    if (s === 'matched') return { text: '候选', color: 'orange', icon: <LinkOutlined /> }
    return { text: '待处理', color: 'red', icon: <CloseCircleOutlined /> }
  }

  // ── Stats ──

  const docTotal = data?.document_items.length ?? 0
  const docMatched = data?.document_items.filter((d) => d.match_status !== 'missing').length ?? 0
  const persTotal = data?.personnel_items.length ?? 0
  const persMatched = data?.personnel_items.filter((p) => p.match_status !== 'missing').length ?? 0
  const total = docTotal + persTotal
  const done = docMatched + persMatched

  const allSelected = [
    ...(data?.document_items ?? []),
    ...(data?.personnel_items ?? []),
  ].filter((it) => it.match_status === 'selected' || it.match_status === 'auto' || it.match_status === 'uploaded')

  // ── Render ──

  return (
    <div>
      {/* Progress overview */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 500 }}>信息搜集进度</span>
          <Progress percent={total > 0 ? Math.round((done / total) * 100) : 100} style={{ flex: 1 }} />
          <span style={{ color: '#666' }}>{done}/{total} 项已完成</span>
        </div>
        <Space>
          <Tag color="green"><CheckCircleOutlined /> 已匹配</Tag>
          <Tag color="red"><CloseCircleOutlined /> 待处理</Tag>
        </Space>
      </Card>

      {/* Document items */}
      {data && data.document_items.length > 0 && (
        <Card title="资质与证件" style={{ marginBottom: 16 }}>
          <List
            loading={loading}
            dataSource={data.document_items}
            renderItem={(item: ResourceMatch) => {
              const meta = statusMeta(item.match_status)
              const isDone = item.match_status === 'selected' || item.match_status === 'uploaded' || item.match_status === 'auto'

              return (
                <List.Item
                  style={isDone ? { background: '#f6ffed', borderLeft: '3px solid #52c41a', paddingLeft: 12 } : {}}
                  actions={[
                    isDone ? (
                      <Tag color={meta.color} icon={meta.icon}>{meta.text}</Tag>
                    ) : (
                      <Space>
                        <Button
                          size="small"
                          icon={<UploadOutlined />}
                          onClick={() => { setUploadReq(item.requirement.name); setUploadOpen(true) }}
                        >
                          上传
                        </Button>
                        <Button
                          size="small"
                          icon={<LinkOutlined />}
                          onClick={() => {
                            setPickerReq(item.requirement.name)
                            setPickerDefaultMode(item.requirement.category === 'contract_performance' ? 'contract' : 'qualification')
                            setPickerOpen(true)
                          }}
                        >
                          从资源库选择
                        </Button>
                      </Space>
                    ),
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <span>
                        <span style={{ marginRight: 8, color: meta.color }}>{meta.icon}</span>
                        {item.requirement.name}
                      </span>
                    }
                    description={
                      <span>
                        {item.matches.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            {item.matches.map((m: any, i: number) => (
                              <Tag
                                key={m.link_id || m.id || i}
                                closable={!!m.link_id}
                                color={m.selection === 'auto' ? 'blue' : 'green'}
                                onClose={async (e) => {
                                  e.preventDefault()
                                  await removeMatch(item, m)
                                }}
                              >
                                {m.name}
                                {m.selection === 'auto' ? '（自动）' : ''}
                              </Tag>
                            ))}
                          </div>
                        )}
                      </span>
                    }
                  />
                </List.Item>
              )
            }}
          />
        </Card>
      )}

      {/* Personnel items */}
      {data && data.personnel_items.length > 0 && (
        <Card title="人员配置" style={{ marginBottom: 16 }}>
          <List
            loading={loading}
            dataSource={data.personnel_items}
            renderItem={(item: ResourceMatch) => {
              const meta = statusMeta(item.match_status)
              const isDone = item.match_status === 'selected' || item.match_status === 'uploaded' || item.match_status === 'auto'

              return (
                <List.Item
                  style={isDone ? { background: '#f6ffed', borderLeft: '3px solid #52c41a', paddingLeft: 12 } : {}}
                  actions={[
                    isDone ? (
                      <Tag color={meta.color} icon={meta.icon}>{meta.text}</Tag>
                    ) : (
                      <Space>
                        <Button
                          size="small"
                          icon={<UserAddOutlined />}
                          onClick={() => {
                            setPickerReq(item.requirement.name)
                            setPickerDefaultMode('personnel')
                            setPickerOpen(true)
                          }}
                        >
                          从人员库选择
                        </Button>
                        <Button
                          size="small"
                          icon={<PlusOutlined />}
                          onClick={() => { setQuickPersonnelRole(item.requirement.name); setQuickPersonnelOpen(true) }}
                        >
                          添加新人员
                        </Button>
                      </Space>
                    ),
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <span>
                        <span style={{ marginRight: 8, color: meta.color }}>{meta.icon}</span>
                        {item.requirement.name}
                        {item.requirement.details && (
                          <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>{item.requirement.details}</span>
                        )}
                      </span>
                    }
                    description={
                      <span>
                        {item.matches.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            {item.matches.map((m: any, i: number) => (
                              <Tag
                                key={m.link_id || m.id || i}
                                closable={!!m.link_id}
                                color={m.selection === 'auto' ? 'blue' : 'green'}
                                onClose={async (e) => {
                                  e.preventDefault()
                                  await removeMatch(item, m)
                                }}
                              >
                                {m.name}
                                {m.selection === 'auto' ? '（自动）' : ''}
                              </Tag>
                            ))}
                          </div>
                        )}
                      </span>
                    }
                  />
                </List.Item>
              )
            }}
          />
        </Card>
      )}

      {/* No requirements found */}
      {data && docTotal === 0 && persTotal === 0 && (
        <Alert
          message="未检测到需要搜集的资质或人员要求"
          description="招标文件中未明确列出所需证件或人员配置，可以直接进入生成步骤。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 已选资源汇总 */}
      {allSelected.length > 0 && (
        <Card title="已选资源汇总" size="small" style={{ marginBottom: 16 }}>
          {allSelected.map((it) => (
            <div key={it.requirement.name} style={{ marginBottom: 8 }}>
              <Space>
                <Tag color="blue">{it.requirement.name}</Tag>
                <span style={{ color: '#666' }}>
                  {it.matches.map((m: any) => m.name).join('、')}
                </span>
              </Space>
            </div>
          ))}
        </Card>
      )}

      {/* 公司信息 */}
      {company && (
        <Card title="公司信息" size="small" style={{ marginBottom: 16 }}>
          <Space wrap>
            <Tag>{company.company_name}</Tag>
            <span style={{ color: '#999' }}>统一社会信用代码：{company.business_license_number || '-'}</span>
            <span style={{ color: '#999' }}>法定代表人：{company.legal_rep_name || '-'}</span>
          </Space>
          <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>生成标书时，公司信息将自动注入所有章节。</div>
        </Card>
      )}

      {/* Action bar */}
      <Card>
        <Space>
          <Button type="primary" size="large" onClick={handleConfirm} loading={confirming}>
            确认并继续
          </Button>
          <Button size="large" onClick={handleSkip} loading={confirming}>
            跳过信息搜集
          </Button>
        </Space>
      </Card>

      {/* Unified Resource Picker Modal */}
      <QualificationPickerModal
        open={pickerOpen}
        requirementName={pickerReq}
        defaultMode={pickerDefaultMode}
        onCancel={() => setPickerOpen(false)}
        onSelectQuals={(list) => handleLinkQuals(list, pickerReq)}
        onSelectPersonnel={(list) => handleAssignPersonnelList(list, pickerReq)}
        onSelectContract={(list) => handleLinkContracts(list, pickerReq)}
        onSelectHistoryBid={(bid) => {
          message.info(`已选择历史投标「${bid.name}」作为参考`)
          setPickerOpen(false)
        }}
      />
      <QuickPersonnelForm
        open={quickPersonnelOpen}
        role={quickPersonnelRole}
        onCancel={() => setQuickPersonnelOpen(false)}
        onCreated={(person, role) => handleAssignPersonnelList([person], role)}
      />
      <QuickQualificationUpload
        open={uploadOpen}
        requirementName={uploadReq}
        projectId={projectId}
        onCancel={() => setUploadOpen(false)}
        onUploaded={() => { setUploadOpen(false); fetchStatus() }}
      />
    </div>
  )
}
