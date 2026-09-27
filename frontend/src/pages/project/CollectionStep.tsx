import { useEffect, useState, useCallback, useMemo } from 'react'
import { Card, Tag, Button, Space, message, Progress, Alert, List, Popconfirm, Upload } from 'antd'
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
import { buildAttachmentOptions } from '../../api/outline'
import type { OutlineAttachment } from '../../api/outline'
import QualificationPickerModal, { PickerMode, PickerSelection } from './QualificationPickerModal'
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

/**
 * 「目录里设成附件类型」的章节 —— 与 document_items（招标要求的需求项）是
 * **两套独立的东西**，只在本页并排展示、各写各的存储：
 * 需求项选择写 ProjectQualification/ProjectContract 关联表；
 * 附件章节选择写 ProjectChapter.chapter_meta_json.attachments（生成期只认它）。
 */
interface ChapterAttachmentRow {
  source: 'chapter_attachment'
  chapter_id: string
  title: string
  order_index: number
  attachments: OutlineAttachment[]
}

type GzRow =
  | { kind: 'req'; item: ResourceMatch }
  | { kind: 'att'; item: ChapterAttachmentRow }

interface CollectionData {
  project_id: string
  status: string
  document_items: ResourceMatch[]
  personnel_items: ResourceMatch[]
  attachment_items?: ChapterAttachmentRow[]
  is_complete: boolean
}

interface Props {
  projectId: string
  onComplete: () => void
}

// 「资质与证件」行资质/合同/人员三路都能挂、后端也都会回显，所以 tab 不设限。
// 「人员配置」行只给人员——资质/合同挂到岗位名下没有任何行会显示它（后端按 role
// 取人员、按 requirement_name 取文档，两个键对不上）。
const DOC_ROW_MODES: PickerMode[] = [
  'qualification', 'contract', 'personnel', 'history_bid', 'company',
]
const PERSONNEL_ROW_MODES: PickerMode[] = ['personnel', 'history_bid', 'company']

// 标签上标出资源类型：一行里可能同时混挂三类，只靠名字分不清。
// 资质是这个卡片的默认预期，不加后缀。
const SOURCE_SUFFIX: Record<string, string> = { contract: '合同', personnel: '人员' }

export default function CollectionStep({ projectId, onComplete }: Props) {
  const [data, setData] = useState<CollectionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)

  // Modal state
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerReq, setPickerReq] = useState('')
  const [pickerDefaultMode, setPickerDefaultMode] = useState<PickerMode>('qualification')
  const [pickerAllowedModes, setPickerAllowedModes] = useState<PickerMode[]>(DOC_ROW_MODES)
  const [quickPersonnelOpen, setQuickPersonnelOpen] = useState(false)
  const [quickPersonnelRole, setQuickPersonnelRole] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadReq, setUploadReq] = useState('')
  const [uploadCategory, setUploadCategory] = useState('')
  const [company, setCompany] = useState<Record<string, any> | null>(null)

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

  // ── 附件章节（目录里设成「附件」的章节）的材料 ──
  const [attachUploading, setAttachUploading] = useState<string | null>(null)
  const [attachPickerTarget, setAttachPickerTarget] = useState<ChapterAttachmentRow | null>(null)

  const attachmentsOf = useCallback(
    (chapterId: string): OutlineAttachment[] =>
      (data?.attachment_items ?? []).find((r) => r.chapter_id === chapterId)?.attachments ?? [],
    [data],
  )

  /** 整份替换该章节的清单。后端会剔除失效项（资源库行已删/路径越界/内容重复）并把剔除的报回来 */
  const putChapterAttachments = async (chapterId: string, next: OutlineAttachment[]) => {
    const res = await client.put(`/collection/${projectId}/chapters/${chapterId}/attachments`, {
      attachments: next,
    })
    const pruned: string[] = res.data?.pruned_attachments ?? []
    if (pruned.length) {
      message.warning(
        `有 ${pruned.length} 项材料已被剔除（资源库行已删除、路径失效或与其它章节重复）：${pruned.join('、')}`,
      )
    }
    fetchStatus()
  }

  const handleAttachUpload = async (chapter: ChapterAttachmentRow, file: File) => {
    setAttachUploading(chapter.chapter_id)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('label', file.name)
      const rec: OutlineAttachment = (
        await client.post(`/bid/${projectId}/attachments/upload`, form)
      ).data
      await putChapterAttachments(chapter.chapter_id, [
        ...attachmentsOf(chapter.chapter_id), rec,
      ])
      message.success(`已挂到「${chapter.title}」`)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '附件上传失败')
    } finally {
      setAttachUploading(null)
    }
  }

  /** 选择器返回的资质/合同/人员对象**不带文件路径**，回查列表拿 path.
   *  复用 buildAttachmentOptions —— 三个库的字段形状由它统一收敛（已用 node 验过）。 */
  const handleAttachPicked = async (picks: PickerSelection) => {
    const chapter = attachPickerTarget
    if (!chapter) return
    const items: OutlineAttachment[] = []
    let pickedCount = 0
    try {
      if (picks.qualifications.length) {
        pickedCount += picks.qualifications.length
        const opts = buildAttachmentOptions('qualification', (await client.get('/qualifications/')).data)
        const byId = new Map(opts.map((o) => [o.value, o]))
        for (const q of picks.qualifications) {
          const o = byId.get(q.id)
          if (o?.path) items.push({ kind: 'qualification', id: o.value, label: o.label, path: o.path })
        }
      }
      if (picks.contracts.length) {
        pickedCount += picks.contracts.length
        const opts = buildAttachmentOptions('contract', (await client.get('/contracts/')).data)
        const byId = new Map(opts.map((o) => [o.value, o]))
        for (const c of picks.contracts) {
          const o = byId.get(c.id)
          if (o?.path) items.push({ kind: 'contract', id: o.value, label: o.label, path: o.path })
        }
      }
      if (picks.personnel.length) {
        pickedCount += picks.personnel.length
        const rows: any[] = (await client.get('/personnel/')).data
        for (const p of picks.personnel) {
          const row = rows.find((r) => r.id === p.id)
          for (const cert of row?.certificates ?? []) {
            if (cert.attachment_path) {
              items.push({
                kind: 'personnel_cert',
                id: cert.id,
                label: `${row.name} · ${cert.cert_name}`,
                path: cert.attachment_path,
              })
            }
          }
        }
      }
    } catch {
      message.error('读取资源库失败')
      return
    }
    if (!items.length) {
      message.warning(pickedCount ? '选中的项都没有扫描件，未挂载' : '没有选中任何项')
      return
    }
    await putChapterAttachments(chapter.chapter_id, [
      ...attachmentsOf(chapter.chapter_id), ...items,
    ])
    message.success(`已挂 ${items.length} 项到「${chapter.title}」`)
  }

  const handleAttachRemove = async (chapter: ChapterAttachmentRow, index: number) => {
    await putChapterAttachments(
      chapter.chapter_id,
      attachmentsOf(chapter.chapter_id).filter((_, i) => i !== index),
    )
  }

  /** 「资质与证件」的合并行：招标需求项在前，目录附件章节在后（用户 2026-09-27 选的形态） */
  const gzRows = useMemo(() => {
    if (!data) return [] as GzRow[]
    return [
      ...(data.document_items ?? []).map((item) => ({ kind: 'req' as const, item })),
      ...(data.attachment_items ?? []).map((item) => ({ kind: 'att' as const, item })),
    ]
  }, [data])

  // 一次确认可能同时带回资质 + 合同 + 人员（跨分类混选），逐类落库后统一刷新一次。
  // 失败时不再逐条提示——整批走同一个 try，避免混选下一半成功一半失败看不出。
  const handleConfirmSelection = async (picks: PickerSelection) => {
    // 从「附件章节」行打开的 → 走附件清单（写 chapter_meta_json.attachments）
    if (attachPickerTarget) {
      setAttachPickerTarget(null)
      setPickerOpen(false)
      await handleAttachPicked(picks)
      return
    }
    const { qualifications, contracts, personnel } = picks
    try {
      for (const q of qualifications) {
        await client.post(`/collection/${projectId}/qualification/link`, {
          qualification_id: q.id,
          requirement_name: pickerReq,
        })
      }
      for (const c of contracts) {
        await client.post(`/collection/${projectId}/contract/link`, {
          contract_id: c.id,
          requirement_name: pickerReq,
        })
      }
      for (const p of personnel) {
        await client.post(`/collection/${projectId}/personnel/assign`, {
          personnel_id: p.id,
          role: pickerReq,
          requirement_desc: pickerReq,
        })
      }
      const total = qualifications.length + contracts.length + personnel.length
      message.success(`已关联 ${total} 项资源`)
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

  // 移除某条匹配。按 match 自己的 source 分派接口——一行里可能混挂资质/合同/人员，
  // 按「行的 category」分派会走错：业绩类需求的 category 实际是 other（parse 侧从
  // 不产出 contract_performance），合同标签会落到 qualification/unlink 上，而它按
  // qualification_id 过滤，永远匹配不到 contract_id —— 表现为 × 点了没反应、也不报错。
  const removeMatch = async (item: ResourceMatch, m: any) => {
    try {
      if (m.source === 'personnel') {
        await client.post(`/collection/${projectId}/personnel/unassign`, { assignment_id: m.link_id })
      } else if (m.source === 'contract') {
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

  // 一行里可能混挂资质/合同/人员，所以标签上标出类型（资质是这卡片的默认预期，不加后缀）
  const renderMatchTags = (item: ResourceMatch) => {
    if (item.matches.length === 0) return null
    return (
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
            {SOURCE_SUFFIX[m.source] ? `（${SOURCE_SUFFIX[m.source]}）` : ''}
            {m.selection === 'auto' ? '（自动）' : ''}
          </Tag>
        ))}
      </div>
    )
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

      {/* 资质与证件：招标要求的需求项 + 目录里设成「附件」的章节，并排展示 */}
      {gzRows.length > 0 && (
        <Card title="资质与证件" style={{ marginBottom: 16 }}>
          <List
            loading={loading}
            dataSource={gzRows}
            renderItem={(row: GzRow) => {
              if (row.kind === 'att') {
                const ch = row.item
                const list = ch.attachments ?? []
                return (
                  <List.Item
                    style={
                      list.length
                        ? { background: '#f6ffed', borderLeft: '3px solid #52c41a', paddingLeft: 12 }
                        : { borderLeft: '3px solid #faad14', paddingLeft: 12 }
                    }
                    actions={[
                      <Space key="actions">
                        <Tag color={list.length ? 'success' : 'warning'}>
                          {list.length ? `已挂 ${list.length} 项` : '待挂材料'}
                        </Tag>
                        <Upload
                          showUploadList={false}
                          beforeUpload={(file) => {
                            // 自己发请求（走 /bid 的附件端点），不让 antd 代传
                            handleAttachUpload(ch, file as unknown as File)
                            return false
                          }}
                        >
                          <Button
                            size="small"
                            icon={<UploadOutlined />}
                            loading={attachUploading === ch.chapter_id}
                          >
                            上传
                          </Button>
                        </Upload>
                        <Button
                          size="small"
                          icon={<LinkOutlined />}
                          onClick={() => {
                            setAttachPickerTarget(ch)
                            setPickerReq(ch.title)
                            setPickerAllowedModes(['qualification', 'contract', 'personnel'])
                            setPickerDefaultMode('qualification')
                            setPickerOpen(true)
                          }}
                        >
                          从资源库选择
                        </Button>
                      </Space>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <span>
                          <Tag color="orange" style={{ marginRight: 8 }}>目录附件</Tag>
                          {ch.title}
                        </span>
                      }
                      description={
                        list.length ? (
                          <Space wrap size={4}>
                            {list.map((a, i) => (
                              <Tag
                                key={`${a.path}-${i}`}
                                closable
                                onClose={(e) => {
                                  e.preventDefault()
                                  handleAttachRemove(ch, i)
                                }}
                              >
                                {a.label || a.path}
                              </Tag>
                            ))}
                          </Space>
                        ) : (
                          <span style={{ color: '#999' }}>
                            这一章生成时按此清单插图：可上传本项目文件，或从资源库选资质 / 合同 / 人员证书
                          </span>
                        )
                      }
                    />
                  </List.Item>
                )
              }
              const item = row.item
              const meta = statusMeta(item.match_status)
              const isDone = item.match_status === 'selected' || item.match_status === 'uploaded' || item.match_status === 'auto'

              return (
                <List.Item
                  style={isDone ? { background: '#f6ffed', borderLeft: '3px solid #52c41a', paddingLeft: 12 } : {}}
                  actions={[
                    <Space key="actions">
                      {isDone && <Tag color={meta.color} icon={meta.icon}>{meta.text}</Tag>}
                      {/* 自动匹配的行以前被锁死：只有标签、没按钮，而自动候选没有 link_id
                          所以标签也没有 ×，想换一份完全无从下手。现在照样给「上传／从资源库
                          选择」——一动手就以手动的为准（后端 _merge_matches 只在无已落库选择
                          时才回落到自动候选，自动的那条由 _pick_auto_occupy 落库、只处理
                          match_status=='auto' 的行，所以你一动它就轮不上了）。 */}
                      {(!isDone || item.match_status === 'auto') && (
                        <>
                          <Button
                            size="small"
                            icon={<UploadOutlined />}
                            onClick={() => {
                              setUploadReq(item.requirement.name)
                              setUploadCategory(item.requirement.category)
                              setUploadOpen(true)
                            }}
                          >
                            上传
                          </Button>
                          <Button
                            size="small"
                            icon={<LinkOutlined />}
                            onClick={() => {
                              setPickerReq(item.requirement.name)
                              setPickerAllowedModes(DOC_ROW_MODES)
                              // parse 侧业绩类需求实际落 category=other（合同/业绩关键词识别），
                              // 与后端 _is_performance_requirement 同口径，否则默认打开资质选择器
                              setPickerDefaultMode(
                                item.requirement.category === 'contract_performance' ||
                                /业绩|合同|类似项目|中标|履约/.test(item.requirement.name)
                                  ? 'contract'
                                  : 'qualification'
                              )
                              setPickerOpen(true)
                            }}
                          >
                            从资源库选择
                          </Button>
                        </>
                      )}
                    </Space>,
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
                      <span>{renderMatchTags(item)}</span>
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
                    <Space key="actions">
                      {isDone && <Tag color={meta.color} icon={meta.icon}>{meta.text}</Tag>}
                      {/* 同「资质与证件」：自动匹配的行也给按钮，一动手就以手动的为准 */}
                      {(!isDone || item.match_status === 'auto') && (
                        <>
                          <Button
                            size="small"
                            icon={<UserAddOutlined />}
                            onClick={() => {
                              setPickerReq(item.requirement.name)
                              setPickerAllowedModes(PERSONNEL_ROW_MODES)
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
                        </>
                      )}
                    </Space>,
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
                      <span>{renderMatchTags(item)}</span>
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
        allowedModes={pickerAllowedModes}
        onCancel={() => setPickerOpen(false)}
        onConfirmSelection={handleConfirmSelection}
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
        category={uploadCategory}
        projectId={projectId}
        onCancel={() => setUploadOpen(false)}
        onUploaded={() => { setUploadOpen(false); fetchStatus() }}
      />
    </div>
  )
}
