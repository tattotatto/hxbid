import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Steps, Form, Input, DatePicker, Button, Upload, message, Progress } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import client from '../../api/client'

const { Dragger } = Upload

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export default function ProjectCreate() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [projectName, setProjectName] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [uploadFileName, setUploadFileName] = useState('')
  const [uploadFileSize, setUploadFileSize] = useState(0)
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
    customRequest: (options) => {
      const { file, onSuccess, onError } = options as any
      const token = localStorage.getItem('token')

      setUploading(true)
      setUploadPercent(0)
      setUploadFileName(file.name)
      setUploadFileSize(file.size)

      const formData = new FormData()
      formData.append('file', file)
      formData.append('project_name', projectName)

      const xhr = new XMLHttpRequest()
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          setUploadPercent(Math.round((e.loaded / e.total) * 100))
        }
      })
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          message.success('招标文件上传解析成功')
          navigate('/projects')
          onSuccess?.(JSON.parse(xhr.responseText))
        } else {
          let detail = '上传解析失败'
          try { detail = JSON.parse(xhr.responseText)?.detail || detail } catch {}
          message.error(detail)
          onError?.(new Error(detail))
        }
        setUploading(false)
      })
      xhr.addEventListener('error', () => {
        message.error('网络错误，请重试')
        setUploading(false)
        onError?.(new Error('Network error'))
      })
      xhr.open('POST', '/api/v1/bid/upload-and-parse')
      xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.send(formData)
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
          {!uploading ? (
            <Dragger {...uploadProps}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽招标文件到此区域</p>
              <p className="ant-upload-hint">
                支持 .docx / .doc / .pdf / .wps 格式，最大 500MB
              </p>
            </Dragger>
          ) : (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <div style={{ fontSize: 16, marginBottom: 12, fontWeight: 500 }}>
                正在上传：{uploadFileName}
              </div>
              <div style={{ color: '#888', marginBottom: 20 }}>
                文件大小：{formatFileSize(uploadFileSize)}
              </div>
              <Progress
                type="circle"
                percent={uploadPercent}
                size={120}
                status={uploadPercent < 100 ? 'active' : 'success'}
              />
              <div style={{ marginTop: 16, color: '#888' }}>
                {uploadPercent < 100
                  ? `上传中 ${uploadPercent}% ...`
                  : '上传完成，正在AI解析...'}
              </div>
            </div>
          )}
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
