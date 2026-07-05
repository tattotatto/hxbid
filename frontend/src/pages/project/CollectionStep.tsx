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
import PersonnelPickerModal from './PersonnelPickerModal'
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
  const [qualPickerOpen, setQualPickerOpen] = useState(false)
  const [qualPickerReq, setQualPickerReq] = useState('')
  const [personnelPickerOpen, setPersonnelPickerOpen] = useState(false)
  const [personnelPickerRole, setPersonnelPickerRole] = useState('')
  const [quickPersonnelOpen, setQuickPersonnelOpen] = useState(false)
  const [quickPersonnelRole, setQuickPersonnelRole] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadReq, setUploadReq] = useState('')

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

  // ── Actions ──

  const handleLinkQual = async (qualId: string, reqName: string) => {
    try {
      await client.post(`/collection/${projectId}/qualification/link`, {
        qualification_id: qualId,
        requirement_name: reqName,
      })
      message.success('已关联资质')
      setQualPickerOpen(false)
      fetchStatus()
    } catch {
      message.error('关联失败')
    }
  }

  const handleAssignPersonnel = async (person: any, role: string) => {
    try {
      await client.post(`/collection/${projectId}/personnel/assign`, {
        personnel_id: person.id,
        role,
        requirement_desc: role,
      })
      message.success(`已分配 ${person.name} 为 ${role}`)
      setPersonnelPickerOpen(false)
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

  // ── Stats ──

  const docTotal = data?.document_items.length ?? 0
  const docMatched = data?.document_items.filter((d) => d.match_status !== 'missing').length ?? 0
  const persTotal = data?.personnel_items.length ?? 0
  const persMatched = data?.personnel_items.filter((p) => p.match_status !== 'missing').length ?? 0
  const total = docTotal + persTotal
  const done = docMatched + persMatched

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
              const isDone = item.match_status !== 'missing'
              const categoryLabel =
                item.requirement.category === 'company' ? '公司证照' :
                item.requirement.category === 'financial' ? '财务证明' :
                item.requirement.category === 'qualification' ? '专业资质' : '其他'

              return (
                <List.Item
                  actions={[
                    isDone ? (
                      <Tag color="green" icon={<CheckCircleOutlined />}>已匹配</Tag>
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
                          onClick={() => { setQualPickerReq(item.requirement.name); setQualPickerOpen(true) }}
                        >
                          从资质库选择
                        </Button>
                      </Space>
                    ),
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <span>
                        {isDone ? <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 8 }} /> :
                         <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />}
                        {item.requirement.name}
                      </span>
                    }
                    description={
                      <span>
                        <Tag>{categoryLabel}</Tag>
                        {isDone && item.matches[0] && (
                          <span style={{ color: '#666', fontSize: 12 }}>
                            {item.matches[0].name}
                            {item.matches[0].cert_number ? ` (${item.matches[0].cert_number})` : ''}
                          </span>
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
              const isDone = item.match_status !== 'missing'
              return (
                <List.Item
                  actions={[
                    isDone ? (
                      <Tag color="green" icon={<CheckCircleOutlined />}>已分配</Tag>
                    ) : (
                      <Space>
                        <Button
                          size="small"
                          icon={<UserAddOutlined />}
                          onClick={() => { setPersonnelPickerRole(item.requirement.name); setPersonnelPickerOpen(true) }}
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
                        {isDone ? <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 8 }} /> :
                         <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />}
                        {item.requirement.name}
                        {item.requirement.details && (
                          <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>{item.requirement.details}</span>
                        )}
                      </span>
                    }
                    description={
                      isDone && item.matches[0] && (
                        <span style={{ color: '#666', fontSize: 12 }}>
                          已选：{item.matches[0].name}
                          {item.matches[0].tags ? ` (${item.matches[0].tags})` : ''}
                        </span>
                      )
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

      {/* Modals */}
      <QualificationPickerModal
        open={qualPickerOpen}
        requirementName={qualPickerReq}
        onCancel={() => setQualPickerOpen(false)}
        onSelect={(qual) => handleLinkQual(qual.id, qualPickerReq)}
      />
      <PersonnelPickerModal
        open={personnelPickerOpen}
        role={personnelPickerRole}
        onCancel={() => setPersonnelPickerOpen(false)}
        onSelect={handleAssignPersonnel}
      />
      <QuickPersonnelForm
        open={quickPersonnelOpen}
        role={quickPersonnelRole}
        onCancel={() => setQuickPersonnelOpen(false)}
        onCreated={handleAssignPersonnel}
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
