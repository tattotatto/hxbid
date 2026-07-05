import { useState } from 'react'
import { Modal, Form, Input, message } from 'antd'
import client from '../../api/client'

interface Props {
  open: boolean
  role: string
  onCancel: () => void
  onCreated: (personnel: any, role: string) => void
}

export default function QuickPersonnelForm({ open, role, onCancel, onCreated }: Props) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const res = await client.post('/personnel/', {
        name: values.name,
        education: values.education || '',
        phone: values.phone || '',
        tags: values.tags || '',
        id_card: '',
        experiences: [],
        certificates: [],
      })
      message.success(`已添加人员：${values.name}`)
      onCreated(res.data, role)
      form.resetFields()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '添加失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="快速添加人员"
      open={open}
      onCancel={onCancel}
      onOk={handleSubmit}
      confirmLoading={saving}
      okText="添加"
    >
      <Form form={form} layout="vertical">
        <Form.Item label="姓名" name="name" rules={[{ required: true, message: '请输入姓名' }]}>
          <Input placeholder="姓名" />
        </Form.Item>
        <Form.Item label="学历" name="education">
          <Input placeholder="如：本科、大专" />
        </Form.Item>
        <Form.Item label="电话" name="phone">
          <Input placeholder="手机号码" />
        </Form.Item>
        <Form.Item label="标签" name="tags" extra="用逗号分隔多个标签，如：保安师证,消防员证">
          <Input placeholder="保安师证, 消防员证" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
