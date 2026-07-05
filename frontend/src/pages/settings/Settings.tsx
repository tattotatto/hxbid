import { useEffect, useState } from 'react'
import { Card, Form, Input, Select, Button, message, Alert, Statistic, Row, Col, Modal, List, Tag, Popconfirm, InputNumber, Descriptions, Space } from 'antd'
import { DatabaseOutlined, ReloadOutlined, FileTextOutlined, EditOutlined, DeleteOutlined, PlusOutlined, ApiOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'
import client from '../../api/client'

const AI_PROVIDERS = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'tongyi', label: '通义千问' },
]

const AI_FORM_KEY = 'ai_settings'
const NOTIFY_FORM_KEY = 'notify_settings'
const SETTINGS_STORAGE_KEY = 'settings'

interface AISettings {
  ai_provider: string
  ai_model: string
  api_key: string
  temperature: number
}

interface NotifySettings {
  wecom_webhook: string
  dingtalk_webhook: string
}

interface AppSettings {
  ai?: AISettings
  notify?: NotifySettings
}

interface VectorStats {
  enabled: boolean
  total_chunks: number
  unique_projects: number
  unique_chapters: number
}

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveSettings(values: AppSettings): void {
  const existing = loadSettings()
  localStorage.setItem(
    SETTINGS_STORAGE_KEY,
    JSON.stringify({ ...existing, ...values }),
  )
}

export default function Settings() {
  const [aiForm] = Form.useForm()
  const [notifyForm] = Form.useForm()
  const [vectorStats, setVectorStats] = useState<VectorStats | null>(null)
  const [vectorStatsLoading, setVectorStatsLoading] = useState(true)
  const [vectorStatsError, setVectorStatsError] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)

  // Template management
  interface Template {
    id: string
    name: string
    description: string
    style_config: Record<string, any>
    is_default: boolean
    updated_at: string
  }
  const [templates, setTemplates] = useState<Template[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(true)
  const [templateModalOpen, setTemplateModalOpen] = useState(false)
  const [editingTemplate, setEditingTemplate] = useState<Template | null>(null)
  const [templateForm] = Form.useForm()
  const [templateSaving, setTemplateSaving] = useState(false)

  useEffect(() => {
    fetchTemplates()
  }, [])

  const fetchTemplates = () => {
    setTemplatesLoading(true)
    client
      .get('/templates/')
      .then((res) => setTemplates(res.data))
      .catch(() => message.error('获取模板列表失败'))
      .finally(() => setTemplatesLoading(false))
  }

  const handleTemplateSave = async () => {
    const values = await templateForm.validateFields()
    setTemplateSaving(true)
    const payload = {
      name: values.name,
      description: values.description || '',
      style_config: {
        body_font_name: values.body_font_name || '宋体',
        body_font_size_pt: values.body_font_size_pt || 12,
        body_line_spacing: values.body_line_spacing || 1.5,
        heading1_font_name: values.heading1_font_name || '黑体',
        heading1_font_size_pt: values.heading1_font_size_pt || 16,
        heading2_font_name: values.heading2_font_name || '黑体',
        heading2_font_size_pt: values.heading2_font_size_pt || 14,
        margin_top_cm: values.margin_top_cm || 2.54,
        margin_bottom_cm: values.margin_bottom_cm || 2.54,
        margin_left_cm: values.margin_left_cm || 3.17,
        margin_right_cm: values.margin_right_cm || 3.17,
        header_text: values.header_text || '云南宏曦科技有限公司',
        footer_text: values.footer_text || '第 X 页 / 共 Y 页',
      },
    }
    try {
      if (editingTemplate) {
        await client.put(`/templates/${editingTemplate.id}`, payload)
        message.success('模板已更新')
      } else {
        await client.post('/templates/', payload)
        message.success('模板已创建')
      }
      setTemplateModalOpen(false)
      fetchTemplates()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存模板失败')
    } finally {
      setTemplateSaving(false)
    }
  }

  const handleTemplateDelete = async (id: string) => {
    try {
      await client.delete(`/templates/${id}`)
      message.success('模板已删除')
      fetchTemplates()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败')
    }
  }

  const openCreateTemplate = () => {
    setEditingTemplate(null)
    templateForm.resetFields()
    templateForm.setFieldsValue({
      body_font_name: '宋体',
      body_font_size_pt: 12,
      body_line_spacing: 1.5,
      heading1_font_name: '黑体',
      heading1_font_size_pt: 16,
      heading2_font_name: '黑体',
      heading2_font_size_pt: 14,
      margin_top_cm: 2.54,
      margin_bottom_cm: 2.54,
      margin_left_cm: 3.17,
      margin_right_cm: 3.17,
      header_text: '云南宏曦科技有限公司',
      footer_text: '第 X 页 / 共 Y 页',
    })
    setTemplateModalOpen(true)
  }

  const openEditTemplate = (tpl: Template) => {
    setEditingTemplate(tpl)
    templateForm.setFieldsValue({
      name: tpl.name,
      description: tpl.description,
      body_font_name: tpl.style_config?.body_font_name || '宋体',
      body_font_size_pt: tpl.style_config?.body_font_size_pt || 12,
      body_line_spacing: tpl.style_config?.body_line_spacing || 1.5,
      heading1_font_name: tpl.style_config?.heading1_font_name || '黑体',
      heading1_font_size_pt: tpl.style_config?.heading1_font_size_pt || 16,
      heading2_font_name: tpl.style_config?.heading2_font_name || '黑体',
      heading2_font_size_pt: tpl.style_config?.heading2_font_size_pt || 14,
      margin_top_cm: tpl.style_config?.margin_top_cm || 2.54,
      margin_bottom_cm: tpl.style_config?.margin_bottom_cm || 2.54,
      margin_left_cm: tpl.style_config?.margin_left_cm || 3.17,
      margin_right_cm: tpl.style_config?.margin_right_cm || 3.17,
      header_text: tpl.style_config?.header_text || '云南宏曦科技有限公司',
      footer_text: tpl.style_config?.footer_text || '第 X 页 / 共 Y 页',
    })
    setTemplateModalOpen(true)
  }

  useEffect(() => {
    const settings = loadSettings()
    if (settings.ai) {
      aiForm.setFieldsValue(settings.ai)
    }
    if (settings.notify) {
      notifyForm.setFieldsValue(settings.notify)
    }
  }, [])

  useEffect(() => {
    fetchVectorStats()
  }, [])

  const fetchVectorStats = () => {
    setVectorStatsLoading(true)
    client
      .get('/bid/vector-stats')
      .then((res) => {
        setVectorStats(res.data)
        setVectorStatsError(false)
      })
      .catch(() => {
        setVectorStatsError(true)
      })
      .finally(() => {
        setVectorStatsLoading(false)
      })
  }

  const handleRebuildIndex = () => {
    setRebuilding(true)
    client
      .post('/bid/rebuild-index')
      .then(() => {
        message.success('索引重建已启动，请稍后刷新查看进度')
        setTimeout(() => {
          fetchVectorStats()
        }, 3000)
      })
      .catch((err) => {
        const detail = err.response?.data?.detail || '重建索引失败'
        message.error(detail)
      })
      .finally(() => {
        setRebuilding(false)
      })
  }

  const handleAISave = () => {
    const values = aiForm.getFieldsValue()
    saveSettings({ ai: values })
    message.success('AI 配置已保存')
  }

  const handleNotifySave = () => {
    const values = notifyForm.getFieldsValue()
    saveSettings({ notify: values })
    message.success('通知配置已保存')
  }

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
      <Card title="AI 模型配置" style={{ flex: '1 1 420px', minWidth: 380 }}>
        <AIModelConfig />
      </Card>

      <Card title="通知配置" style={{ flex: '1 1 340px', minWidth: 300 }}>
        <Form form={notifyForm} layout="vertical">
          <Form.Item label="企业微信 Webhook" name="wecom_webhook">
            <Input placeholder="企业微信机器人 Webhook URL" />
          </Form.Item>
          <Form.Item label="钉钉 Webhook" name="dingtalk_webhook">
            <Input placeholder="钉钉机器人 Webhook URL" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleNotifySave}>
              保存配置
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="向量知识库" style={{ flex: '1 1 340px', minWidth: 300 }}>
        <p style={{ color: '#666', marginBottom: 16 }}>
          向量知识库用于存储历史标书章节的语义索引，在生成新标书时自动检索相似内容作为参考素材。
        </p>

        {vectorStatsError ? (
          <Alert
            message="无法连接向量知识库服务"
            description="请确认后端服务已启动并启用向量存储功能。"
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
          />
        ) : vectorStatsLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            加载中...
          </div>
        ) : vectorStats ? (
          <>
            <Row gutter={24} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <Statistic
                  title="知识库条目"
                  value={vectorStats.enabled ? vectorStats.total_chunks : 0}
                  suffix="条"
                  prefix={<DatabaseOutlined />}
                  valueStyle={vectorStats.enabled ? undefined : { color: '#999' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="项目数"
                  value={vectorStats.enabled ? vectorStats.unique_projects : 0}
                  suffix="个"
                  valueStyle={vectorStats.enabled ? undefined : { color: '#999' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="章节数"
                  value={vectorStats.enabled ? vectorStats.unique_chapters : 0}
                  suffix="个"
                  valueStyle={vectorStats.enabled ? undefined : { color: '#999' }}
                />
              </Col>
            </Row>

            {!vectorStats.enabled && (
              <Alert
                message="向量知识库未启用"
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}
          </>
        ) : null}

        <div style={{ marginTop: 16 }}>
          <Button
            type="primary"
            danger
            icon={<ReloadOutlined />}
            loading={rebuilding}
            onClick={handleRebuildIndex}
          >
            重建索引
          </Button>
        </div>

        <Alert
          message="重建索引可能需要几分钟时间，期间生成服务不受影响。"
          type="info"
          showIcon
          style={{ marginTop: 16 }}
        />
      </Card>

      {/* Template Management */}
      <Card
        title="排版模板管理"
        style={{ flex: '1 1 420px', minWidth: 380 }}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateTemplate}>
            新建模板
          </Button>
        }
      >
        <p style={{ color: '#666', marginBottom: 16 }}>
          排版模板定义标书输出时的字体、字号、行距、页边距等样式。导出标书时可选择不同模板。
        </p>

        <List
          loading={templatesLoading}
          dataSource={templates}
          renderItem={(tpl: Template) => (
            <List.Item
              actions={[
                <Button
                  key="edit"
                  type="link"
                  icon={<EditOutlined />}
                  onClick={() => openEditTemplate(tpl)}
                />,
                <Popconfirm
                  key="delete"
                  title="确定删除此模板？"
                  onConfirm={() => handleTemplateDelete(tpl.id)}
                >
                  <Button
                    type="link"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={tpl.is_default}
                  />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <span>
                    <FileTextOutlined style={{ marginRight: 8 }} />
                    {tpl.name}
                    {tpl.is_default && (
                      <Tag color="blue" style={{ marginLeft: 8 }}>默认</Tag>
                    )}
                  </span>
                }
                description={
                  <span>
                    {tpl.description || '无描述'}
                    <span style={{ marginLeft: 12, color: '#999', fontSize: 12 }}>
                      正文：{tpl.style_config?.body_font_name} {tpl.style_config?.body_font_size_pt}pt
                      {' · '}行距：{tpl.style_config?.body_line_spacing}倍
                    </span>
                  </span>
                }
              />
            </List.Item>
          )}
        />

        {/* Template Create/Edit Modal */}
        <Modal
          title={editingTemplate ? '编辑模板' : '新建模板'}
          open={templateModalOpen}
          onCancel={() => setTemplateModalOpen(false)}
          onOk={handleTemplateSave}
          confirmLoading={templateSaving}
          width={600}
        >
          <Form form={templateForm} layout="vertical">
            <Form.Item label="模板名称" name="name" rules={[{ required: true, message: '请输入模板名称' }]}>
              <Input placeholder="例如：国标默认、紧凑排版、宽松排版" />
            </Form.Item>
            <Form.Item label="描述" name="description">
              <Input.TextArea rows={2} placeholder="模板用途说明" />
            </Form.Item>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="正文字体" name="body_font_name">
                  <Select options={[
                    { value: '宋体', label: '宋体' },
                    { value: '黑体', label: '黑体' },
                    { value: '仿宋', label: '仿宋' },
                    { value: '楷体', label: '楷体' },
                    { value: '微软雅黑', label: '微软雅黑' },
                  ]} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="正文字号 (pt)" name="body_font_size_pt">
                  <InputNumber min={9} max={20} step={0.5} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item label="正文行距 (倍)" name="body_line_spacing">
              <InputNumber min={1.0} max={3.0} step={0.25} />
            </Form.Item>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="一级标题字体" name="heading1_font_name">
                  <Select options={[
                    { value: '黑体', label: '黑体' },
                    { value: '宋体', label: '宋体' },
                    { value: '微软雅黑', label: '微软雅黑' },
                  ]} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="一级标题字号 (pt)" name="heading1_font_size_pt">
                  <InputNumber min={12} max={24} step={0.5} />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="二级标题字体" name="heading2_font_name">
                  <Select options={[
                    { value: '黑体', label: '黑体' },
                    { value: '宋体', label: '宋体' },
                    { value: '微软雅黑', label: '微软雅黑' },
                  ]} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="二级标题字号 (pt)" name="heading2_font_size_pt">
                  <InputNumber min={11} max={20} step={0.5} />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="上边距 (cm)" name="margin_top_cm">
                  <InputNumber min={1.0} max={5.0} step={0.1} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="下边距 (cm)" name="margin_bottom_cm">
                  <InputNumber min={1.0} max={5.0} step={0.1} />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="左边距 (cm)" name="margin_left_cm">
                  <InputNumber min={1.0} max={5.0} step={0.1} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="右边距 (cm)" name="margin_right_cm">
                  <InputNumber min={1.0} max={5.0} step={0.1} />
                </Form.Item>
              </Col>
            </Row>

            <Form.Item label="页眉文字" name="header_text">
              <Input placeholder="公司名称" />
            </Form.Item>
            <Form.Item label="页脚格式" name="footer_text">
              <Input placeholder="第 X 页 / 共 Y 页" />
            </Form.Item>
          </Form>
        </Modal>
      </Card>

      {/* Edit Feedback Rules */}
      <RuleList />

      {/* Fine-tuning Dataset */}
      <DatasetExporter />
    </div>
  )
}

function RuleList() {
  const [rules, setRules] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client
      .get('/feedback/rules')
      .then((res) => setRules(res.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const activeRules = rules.filter((r: any) => r.is_active)
  const pendingRules = rules.filter((r: any) => !r.is_active)

  return (
    <Card title="编辑反馈规则" style={{ flex: '1 1 420px', minWidth: 380 }}>
      <p style={{ color: '#666', marginBottom: 16 }}>
        系统从每次人工编辑中学习写作规则。同类编辑模式出现 ≥3 次后自动升级为生成约束，
        在后续 AI 生成中生效。
      </p>

      {activeRules.length > 0 && (
        <>
          <div style={{ fontWeight: 'bold', marginBottom: 8, color: '#52c41a' }}>
            已激活约束 ({activeRules.length} 条)
          </div>
          <List
            size="small"
            dataSource={activeRules}
            renderItem={(r: any) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <span>
                      <Tag color="green">活跃</Tag>
                      <Tag>{r.edit_type}</Tag>
                      出现 {r.occurrence_count} 次
                    </span>
                  }
                  description={r.rule_text}
                />
              </List.Item>
            )}
          />
        </>
      )}

      {pendingRules.length > 0 && (
        <>
          <div style={{ fontWeight: 'bold', marginBottom: 8, marginTop: 16, color: '#faad14' }}>
            待累积规则 ({pendingRules.length} 条)
          </div>
          <List
            size="small"
            dataSource={pendingRules}
            renderItem={(r: any) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <span>
                      <Tag color="orange">待升级</Tag>
                      <Tag>{r.edit_type}</Tag>
                      出现 {r.occurrence_count}/{3} 次
                    </span>
                  }
                  description={r.rule_text}
                />
              </List.Item>
            )}
          />
        </>
      )}

      {!loading && rules.length === 0 && (
        <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
          暂无编辑规则。完成标书编辑后，点击项目页的"反馈闭环"按钮开始积累。
        </div>
      )}
    </Card>
  )
}

function AIModelConfig() {
  const [providers, setProviders] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState<Record<string, boolean>>({})
  const [testResults, setTestResults] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    client.get('/admin/ai/providers')
      .then((res) => {
        setProviders(res.data)
        const saved = loadSettings()
        if (saved.ai) {
          form.setFieldsValue(saved.ai)
        }
      })
      .catch(() => message.error('获取AI提供商列表失败'))
      .finally(() => setLoading(false))
  }, [])

  // When provider changes, auto-fill the model field with that provider's default
  const handleProviderChange = (providerId: string) => {
    const provider = providers.find((p) => p.id === providerId)
    if (provider) {
      form.setFieldsValue({ ai_model: provider.default_model })
    }
  }

  const handleTest = async (providerId: string) => {
    setTesting((p) => ({ ...p, [providerId]: true }))
    try {
      const model = form.getFieldValue('ai_model')
      const res = await client.post('/admin/ai/test', {
        provider: providerId,
        model: model || undefined,
      })
      setTestResults((p) => ({ ...p, [providerId]: res.data }))
    } catch {
      setTestResults((p) => ({ ...p, [providerId]: { ok: false, error: '请求失败' } }))
    } finally {
      setTesting((p) => ({ ...p, [providerId]: false }))
    }
  }

  const handleSave = async () => {
    const values = form.getFieldsValue()
    saveSettings({ ai: values })

    // Also push the model override to the backend so it takes effect immediately
    setSaving(true)
    try {
      await client.put('/admin/ai/model', { model: values.ai_model || '' })
      message.success('AI 配置已保存，模型已生效')
    } catch {
      message.success('AI 配置已保存（模型将在服务重启后生效）')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Form form={form} layout="vertical" initialValues={{ ai_provider: 'deepseek', ai_model: '', temperature: 0.7 }}>
        <Form.Item label="当前提供商" name="ai_provider">
          <Select
            options={providers.map((p) => ({
              value: p.id,
              label: `${p.label} ${p.configured ? '✅' : '⚠️ 未配置'}`,
            }))}
            onChange={handleProviderChange}
          />
        </Form.Item>
        <Form.Item label="模型" name="ai_model">
          <Input placeholder="输入模型名称，留空则使用默认模型" />
        </Form.Item>
        <Form.Item label="Temperature" name="temperature">
          <Input type="number" min={0} max={2} step={0.1} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" onClick={handleSave} loading={saving}>保存配置</Button>
        </Form.Item>
      </Form>

      <Descriptions title="提供商状态" size="small" column={1} style={{ marginTop: 20 }}>
        {providers.map((p) => {
          const tr = testResults[p.id]
          return (
            <Descriptions.Item key={p.id} label={
              <Space>
                <ApiOutlined />
                {p.label}
                {p.configured ? <Tag color="green">已配置</Tag> : <Tag color="red">未配置</Tag>}
              </Space>
            }>
              <Space direction="vertical" size={4}>
                <span>默认模型：{p.default_model}</span>
                <span>可用：{p.models.join(', ')}</span>
                {tr && (
                  <span style={{ color: tr.ok ? '#52c41a' : '#ff4d4f' }}>
                    {tr.ok ? (
                      <><CheckCircleOutlined /> 连接正常 ({tr.latency_ms}ms, {tr.model})</>
                    ) : (
                      <><CloseCircleOutlined /> {tr.error}</>
                    )}
                  </span>
                )}
                <Button size="small" loading={testing[p.id]} onClick={() => handleTest(p.id)}>
                  测试连接
                </Button>
              </Space>
            </Descriptions.Item>
          )
        })}
      </Descriptions>
    </>
  )
}

function DatasetExporter() {
  const [stats, setStats] = useState<any>(null)
  const [preview, setPreview] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [exportFormat, setExportFormat] = useState('jsonl')

  useEffect(() => {
    client.get('/dataset/stats').then((res) => setStats(res.data)).catch(() => {})
    client.get('/dataset/preview?limit=5').then((res) => setPreview(res.data)).catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleExport = () => {
    setExporting(true)
    const url = `/api/v1/dataset/export?format=${exportFormat}&max_samples=500`
    window.open(url, '_blank')
    setExporting(false)
  }

  return (
    <Card title="LoRA 微调数据导出" style={{ flex: '1 1 420px', minWidth: 380 }}>
      <p style={{ color: '#666', marginBottom: 16 }}>
        将人工编辑定稿的标书章节导出为微调训练数据集，用于 LoRA 微调 DeepSeek 等开源模型。
        导出格式兼容 OpenAI Fine-tuning API、Unsloth、LLaMA-Factory 等工具。
      </p>

      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={8}>
            <Statistic
              title="已编辑章节"
              value={stats.total_edited_chapters}
              suffix="章"
              valueStyle={{ fontSize: 20 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="中标来源"
              value={stats.won_chapters}
              suffix="章"
              valueStyle={{ color: '#52c41a', fontSize: 20 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title={stats.ready_for_finetuning ? '可开始微调 ✅' : '数据不足 ⚠️'}
              value={stats.estimated_training_tokens}
              suffix="tokens"
              valueStyle={{
                color: stats.ready_for_finetuning ? '#52c41a' : '#faad14',
                fontSize: 20,
              }}
            />
          </Col>
        </Row>
      )}

      {preview.length > 0 && (
        <>
          <div style={{ fontWeight: 'bold', marginBottom: 8 }}>样本预览</div>
          <List
            size="small"
            dataSource={preview}
            renderItem={(s: any) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <span>
                      {s.chapter_title}
                      <Tag color={s.bid_result === 'won' ? 'success' : s.bid_result === 'lost' ? 'error' : 'default'} style={{ marginLeft: 8 }}>
                        {s.bid_result || '未知'}
                      </Tag>
                      <Tag>编辑度 {s.edit_score}%</Tag>
                      <Tag color="blue">质量 {s.quality_score}%</Tag>
                    </span>
                  }
                  description={s.output_preview}
                />
              </List.Item>
            )}
          />
        </>
      )}

      <div style={{ marginTop: 16 }}>
        <Space>
          <Select
            value={exportFormat}
            onChange={setExportFormat}
            options={[
              { value: 'jsonl', label: 'JSONL (OpenAI)' },
              { value: 'alpaca', label: 'Alpaca' },
              { value: 'sharegpt', label: 'ShareGPT' },
              { value: 'chatml', label: 'ChatML' },
            ]}
            style={{ width: 160 }}
          />
          <Button
            type="primary"
            onClick={handleExport}
            loading={exporting}
            disabled={!stats?.ready_for_finetuning}
          >
            导出训练数据
          </Button>
        </Space>
      </div>

      <Alert
        message={
          stats?.ready_for_finetuning
            ? `已有 ${stats.total_edited_chapters} 章编辑定稿数据，可开始微调。运行 scripts/lora_finetune.py 开始训练。`
            : `当前仅 ${stats?.total_edited_chapters || 0} 章数据，建议积累 ≥20 章后再微调以保证效果。`
        }
        type={stats?.ready_for_finetuning ? 'success' : 'warning'}
        showIcon
        style={{ marginTop: 16 }}
      />
    </Card>
  )
}
