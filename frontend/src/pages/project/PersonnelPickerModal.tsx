import { useEffect, useState } from 'react'
import { Modal, Table, Button, Input, Select, message } from 'antd'
import client from '../../api/client'

interface Personnel {
  id: string
  name: string
  education: string
  phone: string
  tags: string
}

interface Props {
  open: boolean
  role: string
  onCancel: () => void
  onSelect: (person: Personnel, role: string) => void
}

const ROLE_OPTIONS = [
  { value: '项目负责人', label: '项目负责人' },
  { value: '项目参与人', label: '项目参与人' },
  { value: '保安队长', label: '保安队长' },
  { value: '保安员', label: '保安员' },
  { value: '技术负责人', label: '技术负责人' },
]

export default function PersonnelPickerModal({ open, role, onCancel, onSelect }: Props) {
  const [personnel, setPersonnel] = useState<Personnel[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedRole, setSelectedRole] = useState(role)

  useEffect(() => {
    if (open) {
      setLoading(true)
      setSelectedRole(role)
      client.get('/personnel/')
        .then((res) => setPersonnel(res.data))
        .catch(() => message.error('获取人员列表失败'))
        .finally(() => setLoading(false))
    }
  }, [open, role])

  const filtered = search
    ? personnel.filter((p) =>
        p.name.includes(search) || p.tags.includes(search) || p.phone.includes(search)
      )
    : personnel

  const columns = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '学历', dataIndex: 'education', key: 'education' },
    { title: '电话', dataIndex: 'phone', key: 'phone' },
    { title: '标签', dataIndex: 'tags', key: 'tags' },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Personnel) => (
        <Button type="link" onClick={() => onSelect(record, selectedRole)}>选择</Button>
      ),
    },
  ]

  return (
    <Modal
      title="选择人员"
      open={open}
      onCancel={onCancel}
      footer={null}
      width={700}
    >
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <Input
          placeholder="搜索姓名/标签/电话..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1 }}
          allowClear
        />
        <Select
          value={selectedRole}
          onChange={setSelectedRole}
          options={ROLE_OPTIONS}
          style={{ width: 140 }}
          placeholder="选择角色"
        />
      </div>
      <Table
        dataSource={filtered}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={false}
      />
    </Modal>
  )
}
