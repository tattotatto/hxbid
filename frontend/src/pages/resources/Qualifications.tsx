import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, DatePicker, Popconfirm, message, Space, Upload, Row, Col, Image, Spin } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, UploadOutlined, ScanOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'

interface Qualification {
  id: number
  name: string
  cert_number: string | null
  issuing_authority: string | null
  issue_date: string | null
  expiry_date: string | null
  attachment_path: string | null
}

export default function Qualifications() {
  const [data, setData] = useState<Qualification[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Qualification | null>(null)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [ocrLoading, setOcrLoading] = useState(false)
  const [form] = Form.useForm()

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await client.get('/qualifications/')
      setData(res.data)
    } catch {
      message.error('获取资质列表失败')
    } finally { setLoading(false) }
  }

  useEffect(() => { fetchData() }, [])

  const handleAdd = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (record: Qualification) => {
    setEditing(record)
    form.setFieldsValue({
      ...record,
      issue_date: record.issue_date ? dayjs(record.issue_date) : null,
      expiry_date: record.expiry_date ? dayjs(record.expiry_date) : null,
    })
    setModalOpen(true)
  }

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/qualifications/${id}`)
      message.success('资质已删除')
      fetchData()
    } catch { message.error('删除资质失败') }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setConfirmLoading(true)
      const payload = {
        ...values,
        issue_date: values.issue_date ? values.issue_date.format('YYYY-MM-DD') : null,
        expiry_date: values.expiry_date ? values.expiry_date.format('YYYY-MM-DD') : null,
      }
      if (editing) {
        await client.put(`/qualifications/${editing.id}`, payload)
        message.success('资质已更新')
      } else {
        await client.post('/qualifications/', payload)
        message.success('资质已添加')
      }
      setModalOpen(false)
      fetchData()
    } catch { message.error('操作失败') }
    finally { setConfirmLoading(false) }
  }

  const handleOcr = async (file: File) => {
    setOcrLoading(true)
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await client.post('/qualifications/ocr', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const d = res.data
      // Auto-fill fields from OCR, preserving any existing user input
      const current = form.getFieldsValue()
      form.setFieldsValue({
        name: d.name || current.name || '',
        cert_number: d.cert_number || current.cert_number || '',
        issuing_authority: d.issuing_authority || current.issuing_authority || '',
        issue_date: d.issue_date ? dayjs(d.issue_date) : current.issue_date,
        expiry_date: d.expiry_date ? dayjs(d.expiry_date) : current.expiry_date,
      })
      if (d.ocr_text && d.ocr_text.length > 10) {
        message.success('OCR识别完成，请核对自动填充的字段')
      } else {
        message.warning('未识别到有效文字，请手动填写字段')
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || 'OCR识别失败，请手动填写')
    } finally { setOcrLoading(false) }
  }

  const columns: ColumnsType<Qualification> = [
    { title: '资质名称', dataIndex: 'name', key: 'name' },
    { title: '证书编号', dataIndex: 'cert_number', key: 'cert_number', render: (v: string | null) => v || '-' },
    { title: '颁发机构', dataIndex: 'issuing_authority', key: 'issuing_authority', render: (v: string | null) => v || '-' },
    { title: '到期日期', dataIndex: 'expiry_date', key: 'expiry_date', render: (v: string | null) => v ? dayjs(v).format('YYYY-MM-DD') : '-' },
    { title: '附件', dataIndex: 'attachment_path', key: 'attachment', render: (v: string | null) => v ? '已上传' : '-' },
    {
      title: '操作', key: 'actions',
      render: (_: unknown, record: Qualification) => (
        <Space>
          <Button type="link" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>公司资质</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加资质</Button>
      </div>
      <Table<Qualification> columns={columns} dataSource={data} rowKey="id" loading={loading} />
      <Modal
        title={editing ? '编辑资质' : '添加资质'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={confirmLoading}
        okText="保存" cancelText="取消"
        width={560}
        destroyOnClose
      >
        {/* OCR Upload Section */}
        <div style={{ marginBottom: 16, padding: 12, background: '#fafafa', borderRadius: 8 }}>
          <Row gutter={8} align="middle">
            <Col>
              <Upload
                accept="image/*"
                showUploadList={false}
                beforeUpload={(file) => { handleOcr(file); return false }}
              >
                <Button icon={<ScanOutlined />} loading={ocrLoading}>
                  上传图片自动识别
                </Button>
              </Upload>
            </Col>
            <Col flex="auto">
              <span style={{ color: '#888', fontSize: 12 }}>
                上传资质证书图片，系统自动识别名称、编号、颁发机构、日期等信息，识别后可手动修正
              </span>
            </Col>
          </Row>
        </div>

        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="资质名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="如：保安服务许可证" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="证书编号" name="cert_number">
                <Input placeholder="证书编号" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="颁发机构" name="issuing_authority">
                <Input placeholder="颁发机构" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="发证日期" name="issue_date">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="到期日期" name="expiry_date">
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </div>
  )
}
