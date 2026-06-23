import { useState, useEffect } from 'react'
import { Card, Form, Input, Button, Upload, message, Descriptions, Spin, Row, Col, Image, Divider } from 'antd'
import { UploadOutlined, ScanOutlined, SaveOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload'
import client from '../../api/client'

export default function CompanyInfo() {
  const [profile, setProfile] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  // OCR states
  const [ocrLoading, setOcrLoading] = useState<Record<string, boolean>>({})

  const fetchProfile = () => {
    setLoading(true)
    client.get('/company/')
      .then((res) => {
        setProfile(res.data)
        form.setFieldsValue(res.data)
      })
      .catch(() => message.error('获取公司信息失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchProfile() }, [])

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    const fd = new FormData()
    Object.entries(values).forEach(([k, v]) => {
      if (v !== undefined && v !== null) fd.append(k, String(v))
    })
    try {
      const res = await client.put('/company/', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setProfile(res.data)
      message.success('公司信息已保存')
    } catch {
      message.error('保存失败')
    } finally { setSaving(false) }
  }

  const handleOcr = async (file: File, docType: string) => {
    setOcrLoading((p) => ({ ...p, [docType]: true }))
    const fd = new FormData()
    fd.append('file', file)
    fd.append('doc_type', docType)
    try {
      const res = await client.post('/company/ocr', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const data = res.data
      if (docType === 'business_license') {
        form.setFieldsValue({
          company_name: data.company_name || form.getFieldValue('company_name'),
          business_license_number: data.business_license_number || form.getFieldValue('business_license_number'),
          legal_rep_name: data.legal_rep_name || form.getFieldValue('legal_rep_name'),
          address: data.address || form.getFieldValue('address'),
        })
      }
      if (data.ocr_text) {
        message.info(`OCR识别完成。请核对自动填充的字段是否正确。`)
      } else {
        message.warning('OCR未识别到文字，请手动填写')
      }
    } catch {
      message.error('OCR识别失败')
    } finally { setOcrLoading((p) => ({ ...p, [docType]: false })) }
  }

  const ImageField = ({ label, value, docType }: { label: string; value: string; docType: string }) => (
    <Form.Item label={label}>
      <Row gutter={8} align="middle">
        <Col flex="auto">
          <Upload
            accept="image/*"
            showUploadList={false}
            beforeUpload={(file) => {
              const fd = new FormData()
              fd.append(docType === 'business_license' ? 'business_license_image' :
                       docType === 'id_front' ? 'legal_rep_id_front_image' : 'legal_rep_id_back_image', file)
              client.put('/company/', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
                .then((res) => setProfile(res.data))
                .then(() => message.success('图片已上传'))
              return false
            }}
          >
            <Button icon={<UploadOutlined />} loading={ocrLoading[docType]}>
              {value ? '更换图片' : '上传图片'}
            </Button>
          </Upload>
        </Col>
        <Col>
          <Button
            icon={<ScanOutlined />}
            loading={ocrLoading[docType]}
            onClick={() => {
              const input = document.createElement('input')
              input.type = 'file'
              input.accept = 'image/*'
              input.onchange = (e: any) => {
                const file = e.target.files?.[0]
                if (file) handleOcr(file, docType)
              }
              input.click()
            }}
          >
            OCR识别
          </Button>
        </Col>
      </Row>
      {value && (
        <div style={{ marginTop: 8 }}>
          <Image src={`/uploads/${value.replace(/^uploads[\/\\]/, '')}`} width={200} fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI2VlZSIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkeT0iLjNlbSIgZmlsbD0iIzk5OSI+5Zu+54mH5Yqg6L295aSx6LSlPC90ZXh0Pjwvc3ZnPg==" />
        </div>
      )}
    </Form.Item>
  )

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>公司基本信息</h2>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
          保存信息
        </Button>
      </div>

      <Row gutter={24}>
        <Col span={12}>
          <Card title="工商信息" style={{ marginBottom: 24 }}>
            <Form form={form} layout="vertical">
              <Form.Item label="公司名称" name="company_name" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="统一社会信用代码" name="business_license_number">
                <Input />
              </Form.Item>
              <Form.Item label="法定代表人" name="legal_rep_name">
                <Input />
              </Form.Item>
              <Form.Item label="法定代表人身份证号" name="legal_rep_id_number">
                <Input maxLength={18} />
              </Form.Item>
              <Form.Item label="公司地址" name="address">
                <Input.TextArea rows={2} />
              </Form.Item>
              <Form.Item label="联系电话" name="contact_phone">
                <Input />
              </Form.Item>
              <Form.Item label="备注" name="notes">
                <Input.TextArea rows={2} />
              </Form.Item>
            </Form>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="证照图片" style={{ marginBottom: 24 }}>
            <Form layout="vertical">
              <ImageField label="营业执照" value={profile?.business_license_image} docType="business_license" />
              <Divider />
              <ImageField label="法人身份证（正面）" value={profile?.legal_rep_id_front_image} docType="id_front" />
              <Divider />
              <ImageField label="法人身份证（反面）" value={profile?.legal_rep_id_back_image} docType="id_back" />
            </Form>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
