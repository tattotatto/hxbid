import { useState, useEffect } from 'react'
import { Card, Form, Input, Button, Upload, message, Spin, Row, Col, Image, Divider } from 'antd'
import { UploadOutlined, SaveOutlined } from '@ant-design/icons'
import client from '../../api/client'

export default function CompanyInfo() {
  const [profile, setProfile] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()
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

  // Upload image: save to server, then OCR, fill empty fields only
  const handleImageUpload = async (file: File, imageField: string, docType: string) => {
    setOcrLoading((p) => ({ ...p, [docType]: true }))
    try {
      // 1. Upload image to company profile
      const fd = new FormData()
      fd.append(imageField, file)
      const res = await client.put('/company/', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setProfile(res.data)

      // 2. OCR the image
      const ocrFd = new FormData()
      ocrFd.append('file', file)
      ocrFd.append('doc_type', docType)
      const ocrRes = await client.post('/company/ocr', ocrFd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const d = ocrRes.data

      // 3. Fill only EMPTY fields with OCR results
      const current = form.getFieldsValue()
      if (docType === 'business_license') {
        if (!current.company_name && d.company_name) form.setFieldValue('company_name', d.company_name)
        if (!current.business_license_number && d.business_license_number) form.setFieldValue('business_license_number', d.business_license_number)
        if (!current.legal_rep_name && d.legal_rep_name) form.setFieldValue('legal_rep_name', d.legal_rep_name)
        if (!current.address && d.address) form.setFieldValue('address', d.address)
      } else {
        if (!current.legal_rep_name && d.name) form.setFieldValue('legal_rep_name', d.name)
        if (!current.legal_rep_id_number && d.id_number) form.setFieldValue('legal_rep_id_number', d.id_number)
      }

      if (d.ocr_text && d.ocr_text.length > 10) {
        message.success('图片识别完成，空白字段已自动填充，请核对')
      } else {
        message.info('图片已上传，未能识别到文字内容，请手动填写')
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败')
    } finally {
      setOcrLoading((p) => ({ ...p, [docType]: false }))
    }
  }

  const ImageUpload = ({ label, value, imageField, docType }: {
    label: string; value: string; imageField: string; docType: string
  }) => (
    <Form.Item label={label}>
      <Upload
        accept="image/*"
        showUploadList={false}
        beforeUpload={(file) => {
          handleImageUpload(file, imageField, docType)
          return false
        }}
      >
        <Button icon={<UploadOutlined />} loading={ocrLoading[docType]}>
          {value ? '更换图片（自动识别）' : '上传图片（自动识别）'}
        </Button>
      </Upload>
      {value && (
        <div style={{ marginTop: 8 }}>
          <Image
            src={`/uploads/${value.replace(/^uploads[\/\\]/, '')}`}
            width={220}
            style={{ border: '1px solid #eee', borderRadius: 4 }}
            fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI2VlZSIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkeT0iLjNlbSIgZmlsbD0iIzk5OSI+5Yqg6L295aSx6LSlPC90ZXh0Pjwvc3ZnPg=="
          />
        </div>
      )}
    </Form.Item>
  )

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>公司基本信息</h2>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存信息</Button>
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
          <Card title="证照图片（上传即自动识别填充）" style={{ marginBottom: 24 }}>
            <Form layout="vertical">
              <ImageUpload label="营业执照" value={profile?.business_license_image}
                imageField="business_license_image" docType="business_license" />
              <Divider />
              <ImageUpload label="法人身份证（正面）" value={profile?.legal_rep_id_front_image}
                imageField="legal_rep_id_front_image" docType="id_card" />
              <Divider />
              <ImageUpload label="法人身份证（反面）" value={profile?.legal_rep_id_back_image}
                imageField="legal_rep_id_back_image" docType="id_card" />
            </Form>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
