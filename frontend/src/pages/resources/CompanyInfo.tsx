import { useState, useEffect } from 'react'
import { Card, Form, Input, Button, Upload, message, Spin, Row, Col, Image, Divider } from 'antd'
import { UploadOutlined, SaveOutlined, ScanOutlined } from '@ant-design/icons'
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
      .catch((err) => {
        console.error('获取公司信息失败:', err)
        const detail = err.response?.data?.detail
        const msg = typeof detail === 'string' ? detail
          : Array.isArray(detail) ? detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ')
          : err.response?.status === 500 ? '服务器内部错误，请联系管理员'
          : '获取公司信息失败'
        message.error(msg)
      })
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

  const handleUpload = async (file: File, imageField: string) => {
    const fd = new FormData()
    fd.append(imageField, file)
    try {
      const res = await client.put('/company/', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setProfile(res.data)
      message.success('图片已上传')
    } catch { message.error('上传失败') }
    return false
  }

  // OCR an already-uploaded image
  const handleOcr = async (imagePath: string, docType: string, label: string) => {
    if (!imagePath) {
      message.warning('请先上传' + label)
      return
    }
    setOcrLoading((p) => ({ ...p, [docType]: true }))
    try {
      const res = await client.post('/company/ocr-existing', {
        image_path: imagePath,
        doc_type: docType,
      })
      const d = res.data
      const current = form.getFieldsValue()
      // Only fill empty fields
      if (docType === 'business_license') {
        if (!current.company_name && d.company_name) form.setFieldValue('company_name', d.company_name)
        if (!current.business_license_number && d.business_license_number) form.setFieldValue('business_license_number', d.business_license_number)
        if (!current.legal_rep_name && d.legal_rep_name) form.setFieldValue('legal_rep_name', d.legal_rep_name)
        if (!current.address && d.address) form.setFieldValue('address', d.address)
      } else {
        if (!current.legal_rep_name && d.name) form.setFieldValue('legal_rep_name', d.name)
        if (!current.legal_rep_id_number && d.id_number) form.setFieldValue('legal_rep_id_number', d.id_number)
      }
      const hasResult = d.company_name || d.business_license_number || d.legal_rep_name || d.name || d.id_number || (d.ocr_text && d.ocr_text.length > 10)
      if (hasResult) {
        message.success(label + '识别完成，空白字段已填充')
      } else {
        message.info(label + '：未能识别到文字内容')
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '识别失败')
    } finally { setOcrLoading((p) => ({ ...p, [docType]: false })) }
  }

  const ImageField = ({ label, value, imageField, docType }: {
    label: string; value: string; imageField: string; docType: string
  }) => (
    <Form.Item label={label}>
      <Row gutter={8}>
        <Col>
          <Upload
            accept="image/*"
            showUploadList={false}
            beforeUpload={(file) => handleUpload(file, imageField)}
          >
            <Button icon={<UploadOutlined />}>
              {value ? '更换图片' : '上传图片'}
            </Button>
          </Upload>
        </Col>
        <Col>
          <Button
            icon={<ScanOutlined />}
            loading={ocrLoading[docType]}
            onClick={() => handleOcr(value, docType, label)}
          >
            OCR 识别
          </Button>
        </Col>
      </Row>
      {value ? (
        <div style={{ marginTop: 8 }}>
          <Image
            src={`/uploads/${value.replace(/^uploads[\/\\]/, '')}`}
            width={220}
            style={{ border: '1px solid #eee', borderRadius: 4 }}
          />
        </div>
      ) : (
        <div style={{ marginTop: 8, color: '#bbb', fontSize: 12 }}>未上传</div>
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
              <Form.Item label="公司网站" name="website">
                <Input placeholder="例如：www.hongxi.com" />
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
              <ImageField label="公司 Logo" value={profile?.logo_image}
                imageField="logo_image" docType="logo" />
              <Divider />
              <ImageField label="营业执照" value={profile?.business_license_image}
                imageField="business_license_image" docType="business_license" />
              <Divider />
              <ImageField label="法人身份证（正面）" value={profile?.legal_rep_id_front_image}
                imageField="legal_rep_id_front_image" docType="id_card" />
              <Divider />
              <ImageField label="法人身份证（反面）" value={profile?.legal_rep_id_back_image}
                imageField="legal_rep_id_back_image" docType="id_card" />
            </Form>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
