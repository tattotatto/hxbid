import { useEffect, useState, useRef } from 'react'
import { Card, Table, Button, Modal, Form, Input, InputNumber, Steps, Upload, Image, message, Space, Popconfirm, Tag } from 'antd'
import { PlusOutlined, DeleteOutlined, UploadOutlined, InboxOutlined, EyeOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { UploadProps } from 'antd'
import client from '../../api/client'

const { Dragger } = Upload

interface Contract {
  id: string
  project_name: string
  procurement_unit: string
  procurement_content: string
  contract_amount: string
  service_period: string
  notes: string
  image_paths_json: string
  created_at: string
}

export default function Contracts() {
  const [contracts, setContracts] = useState<Contract[]>([])
  const [loading, setLoading] = useState(true)

  // Create modal (2 steps)
  const [modalOpen, setModalOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [form] = Form.useForm()
  const [creating, setCreating] = useState(false)
  const [createdId, setCreatedId] = useState<string | null>(null)
  const [uploadedPaths, setUploadedPaths] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)

  // Image preview
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewSrc, setPreviewSrc] = useState('')

  // Detail modal
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailContract, setDetailContract] = useState<Contract | null>(null)

  const fetchContracts = () => {
    setLoading(true)
    client.get('/contracts/')
      .then((res) => setContracts(res.data))
      .catch(() => message.error('获取合同列表失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchContracts() }, [])

  // ── Create flow ──

  const openCreate = () => {
    form.resetFields()
    setStep(0)
    setCreatedId(null)
    setUploadedPaths([])
    setModalOpen(true)
  }

  const handleStep1 = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      const res = await client.post('/contracts/', {
        project_name: values.project_name,
        procurement_unit: values.procurement_unit || '',
        procurement_content: values.procurement_content || '',
        contract_amount: values.contract_amount != null ? String(values.contract_amount) : '',
        service_period: values.service_period || '',
        notes: values.notes || '',
      })
      setCreatedId(res.data.id)
      setStep(1)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const handleUpload: UploadProps['customRequest'] = (options) => {
    const { file, onSuccess, onError } = options as any
    if (!createdId) return

    setUploading(true)
    const formData = new FormData()
    formData.append('files', file as File)

    const token = localStorage.getItem('token')
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/v1/contracts/${createdId}/upload`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const data = JSON.parse(xhr.responseText)
        setUploadedPaths((prev) => [...prev, ...data.paths])
        message.success(`「${(file as File).name}」已上传`)
        onSuccess?.(data)
      } else {
        let detail = '上传失败'
        try { detail = JSON.parse(xhr.responseText)?.detail || detail } catch {}
        message.error(detail)
        onError?.(new Error(detail))
      }
      setUploading(false)
    })

    xhr.addEventListener('error', () => {
      message.error('网络错误')
      setUploading(false)
      onError?.(new Error('网络错误'))
    })

    xhr.send(formData)
  }

  const handleFinish = () => {
    setModalOpen(false)
    message.success(`合同「${form.getFieldValue('project_name')}」已保存`)
    fetchContracts()
  }

  // ── Delete ──

  const handleDelete = async (id: string) => {
    try {
      await client.delete(`/contracts/${id}`)
      message.success('合同已删除')
      fetchContracts()
    } catch {
      message.error('删除失败')
    }
  }

  const columns: ColumnsType<Contract> = [
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', ellipsis: true },
    { title: '采购单位', dataIndex: 'procurement_unit', key: 'procurement_unit', ellipsis: true },
    { title: '合同金额', dataIndex: 'contract_amount', key: 'contract_amount', width: 120 },
    { title: '服务时间', dataIndex: 'service_period', key: 'service_period', width: 140 },
    {
      title: '页数',
      key: 'pages',
      width: 60,
      render: (_: any, r: Contract) => {
        try { return JSON.parse(r.image_paths_json || '[]').length } catch { return 0 }
      },
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      render: (d: string) => new Date(d).toLocaleDateString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_: any, r: Contract) => (
        <Space>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => { setDetailContract(r); setDetailOpen(true) }}
          >
            详情
          </Button>
          <Popconfirm title="确定删除此合同？" onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card
        title="历史合同"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加合同</Button>}
      >
        <Table<Contract>
          columns={columns}
          dataSource={contracts}
          rowKey="id"
          loading={loading}
          pagination={false}
        />
      </Card>

      {/* Create Modal — 2 steps */}
      <Modal
        title={step === 0 ? '添加合同 — 填写信息' : '添加合同 — 上传文件'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={
          step === 0 ? [
            <Button key="cancel" onClick={() => setModalOpen(false)}>取消</Button>,
            <Button key="next" type="primary" loading={creating} onClick={handleStep1}>下一步</Button>,
          ] : [
            <Button key="back" onClick={() => setStep(0)}>上一步</Button>,
            <Button key="finish" type="primary" onClick={handleFinish}>完成</Button>,
          ]
        }
        width={640}
        maskClosable={false}
      >
        <Steps
          current={step}
          items={[{ title: '填写合同信息' }, { title: '上传合同文件' }]}
          style={{ marginBottom: 24 }}
        />

        {step === 0 && (
          <Form form={form} layout="vertical">
            <Form.Item label="项目名称" name="project_name" rules={[{ required: true, message: '请输入项目名称' }]}>
              <Input placeholder="合同对应的项目名称" />
            </Form.Item>
            <Form.Item label="采购单位" name="procurement_unit">
              <Input placeholder="甲方单位名称" />
            </Form.Item>
            <Form.Item label="采购内容" name="procurement_content">
              <Input.TextArea rows={2} placeholder="合同主要内容描述" />
            </Form.Item>
            <Form.Item label="合同金额（元）" name="contract_amount">
              <Input placeholder="合同金额" />
            </Form.Item>
            <Form.Item label="服务时间" name="service_period">
              <Input placeholder="如：2024年1月-2025年12月" />
            </Form.Item>
            <Form.Item label="备注" name="notes">
              <Input.TextArea rows={2} placeholder="其他备注信息" />
            </Form.Item>
          </Form>
        )}

        {step === 1 && (
          <div>
            <Dragger
              name="files"
              multiple
              customRequest={handleUpload}
              disabled={uploading}
              accept=".jpg,.jpeg,.png,.bmp,.pdf"
              showUploadList={false}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽文件上传</p>
              <p className="ant-upload-hint">支持 JPG、PNG、BMP、PDF 格式。PDF 自动转为图片。</p>
            </Dragger>

            {uploadedPaths.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Tag color="green">已上传 {uploadedPaths.length} 页</Tag>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                  {uploadedPaths.map((p, i) => (
                    <Image
                      key={i}
                      src={`/uploads/${p.split('/').pop()}`}
                      width={100}
                      style={{ objectFit: 'cover', border: '1px solid #eee', borderRadius: 4 }}
                      fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Detail Modal — show all contract images */}
      <Modal
        title={detailContract?.project_name || '合同详情'}
        open={detailOpen}
        onCancel={() => { setDetailOpen(false); setDetailContract(null) }}
        footer={null}
        width={800}
        style={{ top: 20 }}
      >
        {detailContract && (() => {
          let images: string[] = []
          try { images = JSON.parse(detailContract.image_paths_json || '[]') } catch {}
          if (images.length === 0) return <p style={{ color: '#999', textAlign: 'center' }}>暂无合同图片</p>
          return (
            <div style={{ maxHeight: '70vh', overflowY: 'auto', padding: '0 4px' }}>
              {images.map((p, i) => (
                <div key={i} style={{ marginBottom: 16, textAlign: 'center' }}>
                  <div style={{
                    color: '#888', fontSize: 12, marginBottom: 4,
                    background: '#f5f5f5', padding: '2px 8px', borderRadius: 4,
                    display: 'inline-block'
                  }}>
                    第 {i + 1} / {images.length} 页
                  </div>
                  <Image
                    src={`/uploads/${p.split('/').pop()}`}
                    style={{ maxWidth: '100%', border: '1px solid #eee', borderRadius: 4 }}
                    fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
                  />
                </div>
              ))}
            </div>
          )
        })()}
      </Modal>
    </div>
  )
}
