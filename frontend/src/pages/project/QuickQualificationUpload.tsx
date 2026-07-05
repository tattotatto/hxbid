import { useState } from 'react'
import { Modal, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'

const { Dragger } = Upload

interface Props {
  open: boolean
  requirementName: string
  projectId: string
  onCancel: () => void
  onUploaded: () => void
}

export default function QuickQualificationUpload({ open, requirementName, projectId, onCancel, onUploaded }: Props) {
  const [uploading, setUploading] = useState(false)

  const handleUpload: UploadProps['customRequest'] = (options) => {
    const { file, onSuccess, onError } = options as any
    setUploading(true)

    const formData = new FormData()
    formData.append('file', file as File)
    formData.append('requirement_name', requirementName)

    const token = localStorage.getItem('token')
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/v1/collection/${projectId}/qualification/upload`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        message.success(`「${(file as File).name}」已上传`)
        onUploaded()
        onSuccess?.(JSON.parse(xhr.responseText))
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

  return (
    <Modal
      title={`上传证件 — ${requirementName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
    >
      <Dragger
        name="file"
        multiple={false}
        customRequest={handleUpload}
        disabled={uploading}
        accept=".jpg,.jpeg,.png,.pdf,.bmp"
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
        <p className="ant-upload-hint">支持 JPG、PNG、PDF、BMP 格式</p>
      </Dragger>
    </Modal>
  )
}
