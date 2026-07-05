import { useEffect, useState } from 'react'
import { Modal, Table, Button, Input, message } from 'antd'
import client from '../../api/client'

interface Qualification {
  id: string
  name: string
  cert_number: string
  issuing_authority: string
  expiry_date: string | null
}

interface Props {
  open: boolean
  requirementName: string
  onCancel: () => void
  onSelect: (qual: Qualification) => void
}

export default function QualificationPickerModal({ open, requirementName, onCancel, onSelect }: Props) {
  const [quals, setQuals] = useState<Qualification[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) {
      setLoading(true)
      client.get('/qualifications/')
        .then((res) => setQuals(res.data))
        .catch(() => message.error('获取资质列表失败'))
        .finally(() => setLoading(false))
    }
  }, [open])

  const filtered = search
    ? quals.filter((q) =>
        q.name.includes(search) || q.cert_number.includes(search)
      )
    : quals

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '证书编号', dataIndex: 'cert_number', key: 'cert_number' },
    { title: '发证机构', dataIndex: 'issuing_authority', key: 'issuing_authority' },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Qualification) => (
        <Button type="link" onClick={() => onSelect(record)}>选择</Button>
      ),
    },
  ]

  return (
    <Modal
      title={`选择资质 — ${requirementName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
      width={700}
    >
      <Input
        placeholder="搜索资质名称或编号..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 16 }}
        allowClear
      />
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
