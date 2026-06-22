import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Steps, Form, Input, DatePicker, Button, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import client from '../../api/client'

const { Dragger } = Upload

export default function ProjectCreate() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [projectName, setProjectName] = useState('')
  const [uploading, setUploading] = useState(false)
  const [form] = Form.useForm()

  const handleNext = async () => {
    try {
      const values = await form.validateFields()
      setProjectName(values.name)
      setStep(1)
    } catch {
      // validation failed
    }
  }

  const uploadProps: UploadProps = {
    name: 'file',
    accept: '.docx,.doc,.pdf,.wps',
    showUploadList: false,
    disabled: uploading,
    beforeUpload: async (file) => {
      setUploading(true)
      const formData = new FormData()
      formData.append('file', file)
      formData.append('project_name', projectName)
      try {
        await client.post('/bid/upload-and-parse', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
        message.success('招标文件上传解析成功')
        navigate('/projects')
      } catch {
        message.error('上传解析失败，请重试')
      } finally {
        setUploading(false)
      }
      return false
    },
  }

  return (
    <Card
      title="新建标书项目"
      style={{ maxWidth: 700, margin: '0 auto' }}
    >
      <Steps
        current={step}
        items={[
          { title: '填写信息' },
          { title: '上传招标文件' },
        ]}
        style={{ marginBottom: 32 }}
      />

      {step === 0 && (
        <Form
          form={form}
          layout="vertical"
          style={{ maxWidth: 500, margin: '0 auto' }}
        >
          <Form.Item
            label="项目名称"
            name="name"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="如：XX工业园区2025年度保安服务投标" />
          </Form.Item>
          <Form.Item label="投标截止日期" name="bid_deadline">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleNext} block>
              下一步
            </Button>
          </Form.Item>
        </Form>
      )}

      {step === 1 && (
        <div style={{ maxWidth: 500, margin: '0 auto' }}>
          <Dragger {...uploadProps}>
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽招标文件到此区域</p>
            <p className="ant-upload-hint">
              支持 .docx / .doc / .pdf / .wps 格式
            </p>
          </Dragger>
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Button onClick={() => setStep(0)} disabled={uploading}>
              上一步
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
