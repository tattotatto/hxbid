import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Steps, Upload, message, Space, Popconfirm, Image, Tag, Row, Col } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, UploadOutlined, InboxOutlined, ScanOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { UploadProps } from 'antd'
import client from '../../api/client'

const { Dragger } = Upload

interface Certificate {
  id?: string
  cert_name: string
  cert_number: string
  issuing_authority: string
  attachment_path: string
}

interface Personnel {
  id: string
  name: string
  gender: string
  id_card: string
  education: string
  phone: string
  address: string
  id_valid_from: string
  id_valid_to: string
  tags: string
  id_front_image: string
  id_back_image: string
  health_report_images_json: string
  certificates: Certificate[]
}

export default function Personnel() {
  const [data, setData] = useState<Personnel[]>([])
  const [loading, setLoading] = useState(false)

  // Create modal (2 steps)
  const [modalOpen, setModalOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [form] = Form.useForm()
  const [creatingId, setCreatingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [ocrLoading, setOcrLoading] = useState(false)
  const [idFrontUploaded, setIdFrontUploaded] = useState(false)
  const [idBackUploaded, setIdBackUploaded] = useState(false)

  // Step 2 state
  const [healthUploaded, setHealthUploaded] = useState<string[]>([])
  const [certificates, setCertificates] = useState<Certificate[]>([])
  const [uploading, setUploading] = useState(false)

  const fetchData = async () => {
    setLoading(true)
    try { const res = await client.get('/personnel/'); setData(res.data) }
    catch { message.error('获取人员列表失败') }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchData() }, [])

  // ── Create flow ──

  const openCreate = async () => {
    form.resetFields()
    setStep(0)
    setCreatingId(null)
    setIdFrontUploaded(false)
    setIdBackUploaded(false)
    setHealthUploaded([])
    setCertificates([])
    // Immediately create bare personnel record so ID card upload in step 0 has an ID to use
    try {
      const res = await client.post('/personnel/', {
        name: '',
        gender: '',
        id_card: '',
        education: '',
        phone: '',
        address: '',
        id_valid_from: '',
        id_valid_to: '',
        tags: '',
        experiences: [],
        certificates: [],
      })
      setCreatingId(res.data.id)
      setModalOpen(true)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败')
      return
    }
  }

  const handleCancel = async () => {
    // Clean up bare personnel record if user abandons the flow
    if (creatingId) {
      try { await client.delete(`/personnel/${creatingId}`) } catch { /* best-effort */ }
    }
    setModalOpen(false)
  }

  // Step 1 → Step 2: save all form fields to personnel record
  const handleStep1 = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      await client.put(`/personnel/${creatingId}`, {
        name: values.name || '',
        gender: values.gender || '',
        id_card: values.id_card || '',
        education: values.education || '',
        phone: values.phone || '',
        address: values.address || '',
        id_valid_from: values.id_valid_from || '',
        id_valid_to: values.id_valid_to || '',
        tags: values.tags || '',
      })
      setStep(1)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败')
    } finally { setCreating(false) }
  }

  // OCR ID card and fill form
  const handleIdOcr = async (file: File, side: 'front' | 'back') => {
    if (!creatingId) return
    setOcrLoading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const endpoint = side === 'front'
        ? `/personnel/${creatingId}/upload-id-front`
        : `/personnel/${creatingId}/upload-id-back`
      const res = await client.post(endpoint, fd, { headers: { 'Content-Type': 'multipart/form-data' } })

      // Fill form with OCR results
      const d = res.data
      if (d.name) form.setFieldValue('name', d.name)
      if (d.gender) form.setFieldValue('gender', d.gender)
      if (d.id_card) form.setFieldValue('id_card', d.id_card)
      if (d.address) form.setFieldValue('address', d.address)

      if (side === 'front') setIdFrontUploaded(true)
      else setIdBackUploaded(true)
      message.success(side === 'front' ? '身份证正面已上传并识别' : '身份证反面已上传')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败')
    } finally { setOcrLoading(false) }
  }

  // Step 2: upload health report + certificates
  const handleHealthUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options as any
    if (!creatingId) return
    setUploading(true)
    const fd = new FormData()
    fd.append('files', file as File)
    const token = localStorage.getItem('token')
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/v1/personnel/${creatingId}/upload-health-report`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const resp = JSON.parse(xhr.responseText)
        setHealthUploaded((p) => [...p, ...resp.paths])
        message.success('体检报告已上传')
        onSuccess?.(resp)
      } else { message.error('上传失败'); onError?.(new Error('上传失败')) }
      setUploading(false)
    })
    xhr.addEventListener('error', () => { message.error('网络错误'); setUploading(false) })
    xhr.send(fd)
  }

  const handleCertUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options as any
    if (!creatingId) return
    setUploading(true)
    const fd = new FormData()
    fd.append('file', file as File)
    fd.append('cert_name', (file as File).name)
    const token = localStorage.getItem('token')
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/v1/personnel/${creatingId}/upload-certificate`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const resp = JSON.parse(xhr.responseText)
        setCertificates((p) => [...p, resp])
        message.success('证书已上传')
        onSuccess?.(resp)
      } else { message.error('上传失败'); onError?.(new Error('上传失败')) }
      setUploading(false)
    })
    xhr.addEventListener('error', () => { message.error('网络错误'); setUploading(false) })
    xhr.send(fd)
  }

  const handleFinish = async () => {
    if (!creatingId) return
    // Final safety update: persist all form fields
    const values = form.getFieldsValue()
    try {
      await client.put(`/personnel/${creatingId}`, {
        name: values.name || '',
        gender: values.gender || '',
        id_card: values.id_card || '',
        education: values.education || '',
        phone: values.phone || '',
        address: values.address || '',
        id_valid_from: values.id_valid_from || '',
        id_valid_to: values.id_valid_to || '',
        tags: values.tags || '',
      })
    } catch { /* non-critical */ }
    setModalOpen(false)
    message.success('人员已添加')
    fetchData()
  }

  // ── Delete ──

  const handleDelete = async (id: string) => {
    try { await client.delete(`/personnel/${id}`); message.success('已删除'); fetchData() }
    catch { message.error('删除失败') }
  }

  // ── Columns ──

  const columns: ColumnsType<Personnel> = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '性别', dataIndex: 'gender', key: 'gender', width: 60 },
    { title: '学历', dataIndex: 'education', key: 'education', render: (v: string) => v || '-' },
    { title: '电话', dataIndex: 'phone', key: 'phone', render: (v: string) => v || '-' },
    {
      title: '证书', key: 'certs',
      render: (_: any, r: Personnel) => (r.certificates || []).length > 0
        ? <Tag color="blue">{(r.certificates || []).length} 份</Tag>
        : <Tag>无</Tag>,
    },
    {
      title: '操作', key: 'actions',
      render: (_: any, r: Personnel) => (
        <Popconfirm title="确认删除此人员？" onConfirm={() => handleDelete(r.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>人员管理</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加人员</Button>
      </div>
      <Table<Personnel> columns={columns} dataSource={data} rowKey="id" loading={loading} />

      {/* 2-step create modal */}
      <Modal
        title={step === 0 ? '添加人员 — 身份证信息' : '添加人员 — 上传证照'}
        open={modalOpen}
        onCancel={handleCancel}
        footer={
          step === 0 ? [
            <Button key="cancel" onClick={handleCancel}>取消</Button>,
            <Button key="next" type="primary" loading={creating} onClick={handleStep1}>下一步</Button>,
          ] : [
            <Button key="back" onClick={() => setStep(0)}>上一步</Button>,
            <Button key="finish" type="primary" onClick={handleFinish}>完成</Button>,
          ]
        }
        width={700}
        maskClosable={false}
      >
        <Steps
          current={step}
          items={[{ title: '上传身份证' }, { title: '上传体检报告与证书' }]}
          style={{ marginBottom: 24 }}
        />

        {step === 0 && (
          <div>
            {/* ID card upload area */}
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={12}>
                <div style={{ textAlign: 'center', marginBottom: 4, fontWeight: 500 }}>身份证正面</div>
                <Upload
                  accept="image/*"
                  showUploadList={false}
                  beforeUpload={(file) => { handleIdOcr(file, 'front'); return false }}
                >
                  <Button icon={<ScanOutlined />} loading={ocrLoading} block>
                    {idFrontUploaded ? '✓ 已上传并识别' : '上传并OCR识别'}
                  </Button>
                </Upload>
              </Col>
              <Col span={12}>
                <div style={{ textAlign: 'center', marginBottom: 4, fontWeight: 500 }}>身份证反面</div>
                <Upload
                  accept="image/*"
                  showUploadList={false}
                  beforeUpload={(file) => { handleIdOcr(file, 'back'); return false }}
                >
                  <Button icon={<UploadOutlined />} loading={ocrLoading} block>
                    {idBackUploaded ? '✓ 已上传' : '上传反面'}
                  </Button>
                </Upload>
              </Col>
            </Row>

            {/* Form fields */}
            <Form form={form} layout="vertical">
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item label="姓名" name="name">
                    <Input placeholder="OCR自动识别" />
                  </Form.Item>
                </Col>
                <Col span={4}>
                  <Form.Item label="性别" name="gender">
                    <Input placeholder="识别" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="身份证号" name="id_card">
                    <Input placeholder="OCR自动识别" maxLength={18} />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="住址" name="address">
                <Input placeholder="OCR自动识别" />
              </Form.Item>
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item label="有效期起" name="id_valid_from">
                    <Input placeholder="如2020.01.01" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label="有效期止" name="id_valid_to">
                    <Input placeholder="如2040.01.01" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label="联系电话" name="phone">
                    <Input placeholder="手机号" />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item label="学历" name="education">
                    <Input placeholder="如本科" />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item label="标签" name="tags">
                    <Input placeholder="如: 保安师证, 消防员证" />
                  </Form.Item>
                </Col>
              </Row>
            </Form>
          </div>
        )}

        {step === 1 && (
          <div>
            {/* Health report upload */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>体检报告（可上传多张）</div>
              <Dragger
                name="files"
                multiple
                customRequest={handleHealthUpload}
                disabled={uploading}
                accept=".jpg,.jpeg,.png,.pdf"
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖拽上传体检报告</p>
                <p className="ant-upload-hint">支持图片和PDF</p>
              </Dragger>
              {healthUploaded.length > 0 && (
                <Tag color="green" style={{ marginTop: 8 }}>已上传 {healthUploaded.length} 份</Tag>
              )}
            </div>

            {/* Certificate upload */}
            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>证书（可上传多个）</div>
              <Dragger
                name="file"
                multiple
                customRequest={handleCertUpload}
                disabled={uploading}
                accept=".jpg,.jpeg,.png,.pdf"
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖拽上传证书</p>
                <p className="ant-upload-hint">每个文件作为一份证书保存</p>
              </Dragger>
              {certificates.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <Tag color="blue">已上传 {certificates.length} 份证书</Tag>
                  {certificates.map((c, i) => (
                    <Tag key={i}>{c.cert_name}</Tag>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
