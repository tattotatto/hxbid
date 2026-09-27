import React, { useState } from 'react'
import {
  Drawer,
  Space,
  Tag,
  Alert,
  List,
  Typography,
  Empty,
  Spin,
  Button,
  Upload,
  Modal,
  Select,
  message,
} from 'antd'
import {
  UploadOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import { buildAttachmentOptions, outlineApi } from '../../api/outline'
import type {
  AttachmentPickerOption,
  OutlineAttachment,
  OutlineMatchCandidate,
  OutlineMatchResult,
} from '../../api/outline'

interface MatchDrawerProps {
  open: boolean
  loading: boolean
  projectId: string
  title: string
  result: OutlineMatchResult | null
  attachments: OutlineAttachment[]
  onAttachmentsChange: (next: OutlineAttachment[]) => void
  onPick: (c: OutlineMatchCandidate) => void
  onClose: () => void
}

const KIND_LABEL: Record<string, string> = {
  qualification: '资质',
  personnel_cert: '人员证书',
  contract: '合同',
  upload: '本项目上传',
}

const KIND_ENDPOINT: Record<string, string> = {
  qualification: '/qualifications',
  personnel_cert: '/personnel',
  contract: '/contracts',
}

const STATUS_META: Record<string, { color: string; text: string }> = {
  matched: { color: 'success', text: '已匹配' },
  ambiguous: { color: 'warning', text: '多个候选' },
  missing: { color: 'error', text: '未匹配' },
  na: { color: 'default', text: '不适用（AI 生成）' },
}

const SOURCE_LABEL: Record<string, string> = {
  format_section: '投标文件格式章节',
  full_text: '招标文件全文',
  table: '招标文件表格',
}

const MatchDrawer: React.FC<MatchDrawerProps> = ({
  open,
  loading,
  projectId,
  title,
  result,
  attachments,
  onAttachmentsChange,
  onPick,
  onClose,
}) => {
  const meta = result ? STATUS_META[result.status] ?? STATUS_META.missing : null

  const [uploading, setUploading] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerKind, setPickerKind] = useState<OutlineAttachment['kind']>('qualification')
  const [options, setOptions] = useState<AttachmentPickerOption[]>([])
  const [picked, setPicked] = useState<string[]>([])

  const onUpload = async (file: File) => {
    setUploading(true)
    try {
      const att = await outlineApi.uploadAttachment(projectId, file, file.name)
      onAttachmentsChange([...attachments, att])
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '附件上传失败')
    } finally {
      setUploading(false)
    }
  }

  const openPicker = async (kind: OutlineAttachment['kind']) => {
    setPickerKind(kind)
    setPicked([])
    try {
      const res = await client.get(KIND_ENDPOINT[kind])
      setOptions(buildAttachmentOptions(kind, Array.isArray(res.data) ? res.data : []))
      setPickerOpen(true)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '资源库加载失败')
    }
  }

  const confirmPick = () => {
    const chosen = options.filter((o) => picked.includes(o.value) && o.path)
    const skipped = options.filter((o) => picked.includes(o.value) && !o.path)
    if (skipped.length > 0) {
      message.warning(`${skipped.length} 项没有扫描件/图片，已跳过`)
    }
    onAttachmentsChange([
      ...attachments,
      ...chosen.map((o) => ({
        kind: pickerKind,
        id: o.value,
        label: o.label,
        path: o.path,
      })),
    ])
    setPickerOpen(false)
  }

  return (
    <Drawer
      title={`「${title}」的原文匹配`}
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      ) : !result ? (
        <Empty description="尚未匹配" />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Tag color={meta!.color}>{meta!.text}</Tag>
            {result.source && <Tag>{SOURCE_LABEL[result.source] ?? result.source}</Tag>}
            {result.best?.page != null && <Tag color="blue">第 {result.best.page} 页</Tag>}
            {result.picked === 'manual' && <Tag color="cyan">已手动指定</Tag>}
          </Space>

          {result.status === 'missing' && (
            <Alert
              type="error"
              showIcon
              message="招标文件里没找到这一节"
              description="该章节不会照抄招标原文，将由 AI 撰写。建议改标题、改类型，或从下方候选中选一个。"
            />
          )}
          {result.status === 'ambiguous' && (
            <Alert
              type="warning"
              showIcon
              message="有多个候选，请确认用哪一个"
              description="点选下面任意一条即固化该选择，生成时就用它。"
            />
          )}

          {(result.best || result.table_preview) && (
            <>
              <Typography.Text strong>
                {result.best ? `当前命中：${result.best.raw}` : '命中的招标表格'}
              </Typography.Text>
              <div
                style={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 320,
                  overflow: 'auto',
                  background: '#fafafa',
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: 12,
                  fontSize: 12,
                }}
              >
                {result.best ? result.best.preview : result.table_preview}
                {result.best?.preview_truncated && (
                  <div style={{ color: '#999', marginTop: 8 }}>
                    （预览已截断，生成时使用完整原文）
                  </div>
                )}
              </div>
            </>
          )}

          {result.candidates.length > 0 && (
            <List
              size="small"
              header={`候选（${result.candidates.length}）`}
              dataSource={result.candidates}
              renderItem={(c) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => onPick(c)}
                  actions={[
                    <Typography.Text type="secondary" key="s">
                      {c.score.toFixed(2)}
                    </Typography.Text>,
                  ]}
                >
                  <Space>
                    <Tag>层级 {c.level}</Tag>
                    <span>{c.raw}</span>
                    {c.page != null && <Tag color="blue">第 {c.page} 页</Tag>}
                  </Space>
                </List.Item>
              )}
            />
          )}

          <div>
            <Space style={{ marginBottom: 8 }} wrap>
              <Typography.Text strong>附件清单</Typography.Text>
              <Upload
                showUploadList={false}
                beforeUpload={(file) => {
                  onUpload(file as unknown as File)
                  return false // 自己发请求，不让 antd 代传
                }}
              >
                <Button size="small" icon={<UploadOutlined />} loading={uploading}>
                  上传本项目文件
                </Button>
              </Upload>
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('qualification')}>
                选资质
              </Button>
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('personnel_cert')}>
                选人员证书
              </Button>
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('contract')}>
                选合同
              </Button>
            </Space>
            <List
              size="small"
              locale={{ emptyText: '尚未挂载任何附件（该章节将渲染为占位页）' }}
              dataSource={attachments}
              renderItem={(a, i) => (
                <List.Item
                  actions={[
                    <Button
                      key="del"
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => onAttachmentsChange(attachments.filter((_, k) => k !== i))}
                    />,
                  ]}
                >
                  <Space>
                    <Tag>{KIND_LABEL[a.kind] ?? a.kind}</Tag>
                    <span>{a.label}</span>
                  </Space>
                </List.Item>
              )}
            />
          </div>
        </Space>
      )}

      <Modal
        title="从资源库选附件"
        open={pickerOpen}
        onOk={confirmPick}
        onCancel={() => setPickerOpen(false)}
        okText={`添加 ${picked.length} 项`}
        okButtonProps={{ disabled: picked.length === 0 }}
      >
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          placeholder="选择要挂到本章的资质 / 证书 / 合同扫描件"
          value={picked}
          onChange={setPicked}
          options={options.map((o) => ({
            value: o.value,
            label: o.path ? o.label : `${o.label}（无扫描件）`,
            disabled: !o.path,
          }))}
        />
      </Modal>
    </Drawer>
  )
}

export default MatchDrawer
