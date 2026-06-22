import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, Popconfirm, message, Space } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

interface Personnel {
  id: number
  name: string
  education: string | null
  phone: string | null
  tags: string | null
}

export default function Personnel() {
  const [data, setData] = useState<Personnel[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Personnel | null>(null)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await client.get('/personnel/')
      setData(res.data)
    } catch {
      message.error('获取人员列表失败')
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

  const handleEdit = (record: Personnel) => {
    setEditing(record)
    form.setFieldsValue(record)
    setModalOpen(true)
  }

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/personnel/${id}`)
      message.success('人员已删除')
      fetchData()
    } catch {
      message.error('删除人员失败')
    }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setConfirmLoading(true)
      if (editing) {
        await client.put(`/personnel/${editing.id}`, values)
        message.success('人员已更新')
      } else {
        await client.post('/personnel/', {
          ...values,
          experiences: [],
          certificates: [],
        })
        message.success('人员已添加')
      }
      setModalOpen(false)
      fetchData()
    } catch {
      message.error('操作失败')
    } finally {
      setConfirmLoading(false)
    }
  }

  const columns: ColumnsType<Personnel> = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    {
      title: '学历',
      dataIndex: 'education',
      key: 'education',
      render: (val: string | null) => val || '-',
    },
    {
      title: '联系电话',
      dataIndex: 'phone',
      key: 'phone',
      render: (val: string | null) => val || '-',
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      render: (val: string | null) => val || '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Personnel) => (
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
            description="确定要删除这名人员吗？"
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
        <h2 style={{ margin: 0 }}>人员管理</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加人员
        </Button>
      </div>
      <Table<Personnel>
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
      />
      <Modal
        title={editing ? '编辑人员' : '添加人员'}
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
            label="姓名"
            name="name"
            rules={[{ required: true, message: '请输入姓名' }]}
          >
            <Input placeholder="姓名" />
          </Form.Item>
          <Form.Item label="学历" name="education">
            <Input placeholder="如：本科" />
          </Form.Item>
          <Form.Item label="联系电话" name="phone">
            <Input placeholder="联系电话" />
          </Form.Item>
          <Form.Item label="标签" name="tags">
            <Input placeholder="如: 工业园区,消防管理,大型活动安保" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
