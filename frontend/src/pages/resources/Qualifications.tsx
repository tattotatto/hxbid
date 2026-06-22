import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, DatePicker, Popconfirm, message, Space } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
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
}

export default function Qualifications() {
  const [data, setData] = useState<Qualification[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Qualification | null>(null)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await client.get('/qualifications/')
      setData(res.data)
    } catch {
      message.error('获取资质列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

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
    } catch {
      message.error('删除资质失败')
    }
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
    } catch {
      message.error('操作失败')
    } finally {
      setConfirmLoading(false)
    }
  }

  const columns: ColumnsType<Qualification> = [
    { title: '资质名称', dataIndex: 'name', key: 'name' },
    {
      title: '证书编号',
      dataIndex: 'cert_number',
      key: 'cert_number',
      render: (val: string | null) => val || '-',
    },
    {
      title: '颁发机构',
      dataIndex: 'issuing_authority',
      key: 'issuing_authority',
      render: (val: string | null) => val || '-',
    },
    {
      title: '到期日期',
      dataIndex: 'expiry_date',
      key: 'expiry_date',
      render: (val: string | null) => (val ? dayjs(val).format('YYYY-MM-DD') : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Qualification) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除"
            description="确定要删除这项资质吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <h2 style={{ margin: 0 }}>公司资质</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加资质
        </Button>
      </div>
      <Table<Qualification>
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
      />
      <Modal
        title={editing ? '编辑资质' : '添加资质'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={confirmLoading}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            label="资质名称"
            name="name"
            rules={[{ required: true, message: '请输入资质名称' }]}
          >
            <Input placeholder="如：保安服务许可证" />
          </Form.Item>
          <Form.Item label="证书编号" name="cert_number">
            <Input placeholder="证书编号" />
          </Form.Item>
          <Form.Item label="颁发机构" name="issuing_authority">
            <Input placeholder="颁发机构" />
          </Form.Item>
          <Form.Item label="发证日期" name="issue_date">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="到期日期" name="expiry_date">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
