import { useEffect, useState } from 'react'
import { Modal, Table, Button, Input, Select, message } from 'antd'
import client from '../../api/client'

interface Qualification {
  id: string
  name: string
  cert_number: string
  issuing_authority: string
  expiry_date: string | null
}

interface Contract {
  id: string
  project_name: string
  procurement_unit: string
  contract_amount: string
  contract_date: string | null
  service_period: string
}

type PickerMode = 'qualification' | 'contract'

interface Props {
  open: boolean
  requirementName: string
  onCancel: () => void
  onSelectQual: (qual: Qualification) => void
  onSelectContract?: (contract: Contract) => void
}

export default function QualificationPickerModal({
  open, requirementName, onCancel, onSelectQual, onSelectContract,
}: Props) {
  const [mode, setMode] = useState<PickerMode>('qualification')
  const [quals, setQuals] = useState<Qualification[]>([])
  const [contracts, setContracts] = useState<Contract[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) {
      setLoading(true)
      Promise.all([
        client.get('/qualifications/'),
        client.get('/contracts/'),
      ])
        .then(([qualRes, contractRes]) => {
          setQuals(qualRes.data)
          setContracts(contractRes.data)
        })
        .catch(() => message.error('获取资源列表失败'))
        .finally(() => setLoading(false))
    }
  }, [open])

  const filteredQuals = search
    ? quals.filter((q) =>
        q.name.includes(search) || q.cert_number.includes(search)
      )
    : quals

  const filteredContracts = search
    ? contracts.filter((c) =>
        c.project_name.includes(search) || c.procurement_unit.includes(search)
      )
    : contracts

  const qualColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '证书编号', dataIndex: 'cert_number', key: 'cert_number' },
    { title: '发证机构', dataIndex: 'issuing_authority', key: 'issuing_authority' },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Qualification) => (
        <Button type="link" onClick={() => onSelectQual(record)}>选择</Button>
      ),
    },
  ]

  const contractColumns = [
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', ellipsis: true },
    { title: '采购单位', dataIndex: 'procurement_unit', key: 'procurement_unit', ellipsis: true },
    { title: '合同金额', dataIndex: 'contract_amount', key: 'contract_amount', width: 120 },
    {
      title: '签订日期',
      dataIndex: 'contract_date',
      key: 'contract_date',
      width: 110,
      render: (d: string | null) => d || '-',
    },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Contract) => (
        <Button
          type="link"
          onClick={() => onSelectContract?.(record)}
        >
          选择
        </Button>
      ),
    },
  ]

  return (
    <Modal
      title={`选择资源 — ${requirementName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
      width={mode === 'contract' ? 800 : 700}
    >
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <span style={{ whiteSpace: 'nowrap' }}>选择类型：</span>
        <Select
          value={mode}
          onChange={(v) => { setMode(v); setSearch('') }}
          style={{ width: 140 }}
          options={[
            { value: 'qualification', label: '公司资质' },
            { value: 'contract', label: '历史合同' },
          ]}
        />
        <Input
          placeholder={mode === 'qualification' ? '搜索资质名称或编号...' : '搜索项目名称或采购单位...'}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          style={{ flex: 1 }}
        />
      </div>

      {mode === 'qualification' ? (
        <Table
          dataSource={filteredQuals}
          columns={qualColumns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      ) : (
        <Table
          dataSource={filteredContracts}
          columns={contractColumns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      )}
    </Modal>
  )
}
