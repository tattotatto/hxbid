import { useEffect, useState } from 'react'
import { Modal, Table, Button, Input, Segmented, message, Descriptions } from 'antd'
import client from '../../api/client'

interface Qualification {
  id: string
  name: string
  cert_number: string
  issuing_authority: string
  expiry_date: string | null
}

interface Personnel {
  id: string
  name: string
  gender: string
  education: string
  phone: string
  tags: string
}

interface Contract {
  id: string
  project_name: string
  procurement_unit: string
  contract_amount: string
  contract_date: string | null
  service_period: string
}

interface HistoryBid {
  id: string
  name: string
  status: string
  bid_deadline: string | null
  created_at: string
}

interface CompanyProfile {
  company_name: string
  business_license_number: string
  legal_rep_name: string
  legal_rep_id_number: string
  address: string
  contact_phone: string
  website: string
}

type PickerMode = 'qualification' | 'personnel' | 'contract' | 'history_bid' | 'company'

interface Props {
  open: boolean
  requirementName: string
  /** Default mode when opening — "从资质库选择" opens in qualification, "从人员库选择" in personnel */
  defaultMode?: PickerMode
  onCancel: () => void
  onSelectQual: (qual: Qualification) => void
  onSelectPersonnel?: (person: Personnel) => void
  onSelectContract?: (contract: Contract) => void
  onSelectHistoryBid?: (bid: HistoryBid) => void
}

export default function QualificationPickerModal({
  open, requirementName, defaultMode, onCancel,
  onSelectQual, onSelectPersonnel, onSelectContract, onSelectHistoryBid,
}: Props) {
  const [mode, setMode] = useState<PickerMode>(defaultMode ?? 'qualification')
  const [quals, setQuals] = useState<Qualification[]>([])
  const [personnel, setPersonnel] = useState<Personnel[]>([])
  const [contracts, setContracts] = useState<Contract[]>([])
  const [historyBids, setHistoryBids] = useState<HistoryBid[]>([])
  const [company, setCompany] = useState<CompanyProfile | null>(null)
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) {
      setMode(defaultMode ?? 'qualification')
      setSearch('')
      setLoading(true)
      Promise.all([
        client.get('/qualifications/'),
        client.get('/personnel/'),
        client.get('/contracts/'),
        client.get('/projects/'),
        client.get('/company/').catch(() => ({ data: null })),
      ])
        .then(([qualRes, persRes, contractRes, projRes, companyRes]) => {
          setQuals(qualRes.data)
          setPersonnel(persRes.data)
          setContracts(contractRes.data)
          // Filter for history bids (archived/won/lost/exported)
          const relevantStatuses = ['exported', 'archived', 'won', 'lost']
          setHistoryBids(
            (projRes.data as HistoryBid[]).filter((p) => relevantStatuses.includes(p.status))
          )
          setCompany(companyRes.data)
        })
        .catch(() => message.error('获取资源列表失败'))
        .finally(() => setLoading(false))
    }
  }, [open, defaultMode])

  // ── Filters ──

  const filteredQuals = search
    ? quals.filter((q) => q.name.includes(search) || q.cert_number.includes(search))
    : quals

  const filteredPersonnel = search
    ? personnel.filter((p) => p.name.includes(search) || (p.tags || '').includes(search))
    : personnel

  const filteredContracts = search
    ? contracts.filter((c) =>
        c.project_name.includes(search) || c.procurement_unit.includes(search)
      )
    : contracts

  const filteredHistoryBids = search
    ? historyBids.filter((b) => b.name.includes(search))
    : historyBids

  // ── Columns ──

  const qualColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '证书编号', dataIndex: 'cert_number', key: 'cert_number' },
    { title: '发证机构', dataIndex: 'issuing_authority', key: 'issuing_authority' },
    { title: '', key: 'action', render: (_: any, r: Qualification) => (
      <Button type="link" onClick={() => onSelectQual(r)}>选择</Button>
    )},
  ]

  const personnelColumns = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '学历', dataIndex: 'education', key: 'education', width: 80 },
    { title: '标签', dataIndex: 'tags', key: 'tags', ellipsis: true },
    { title: '电话', dataIndex: 'phone', key: 'phone', width: 120 },
    { title: '', key: 'action', render: (_: any, r: Personnel) => (
      <Button type="link" onClick={() => onSelectPersonnel?.(r)}>选择</Button>
    )},
  ]

  const contractColumns = [
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', ellipsis: true },
    { title: '采购单位', dataIndex: 'procurement_unit', key: 'procurement_unit', ellipsis: true },
    { title: '合同金额', dataIndex: 'contract_amount', key: 'contract_amount', width: 120 },
    { title: '签订日期', dataIndex: 'contract_date', key: 'contract_date', width: 110,
      render: (d: string | null) => d || '-' },
    { title: '', key: 'action', render: (_: any, r: Contract) => (
      <Button type="link" onClick={() => onSelectContract?.(r)}>选择</Button>
    )},
  ]

  const historyBidColumns = [
    { title: '项目名称', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => {
        const map: Record<string, { text: string; color: string }> = {
          won: { text: '已中标', color: 'green' },
          lost: { text: '未中标', color: 'red' },
          exported: { text: '已完成', color: 'blue' },
          archived: { text: '已归档', color: 'default' },
        }
        const info = map[s] || { text: s, color: 'default' }
        return <span style={{ color: info.color }}>{info.text}</span>
      },
    },
    { title: '截止日期', dataIndex: 'bid_deadline', key: 'bid_deadline', width: 110,
      render: (d: string | null) => d || '-' },
    { title: '', key: 'action', render: (_: any, r: HistoryBid) => (
      <Button type="link" onClick={() => onSelectHistoryBid?.(r)}>选择</Button>
    )},
  ]

  // ── Search placeholder ──

  const searchPlaceholders: Record<PickerMode, string> = {
    qualification: '搜索资质名称或编号...',
    personnel: '搜索姓名或标签...',
    contract: '搜索项目名称或采购单位...',
    history_bid: '搜索项目名称...',
    company: '',
  }

  // ── Render ──

  const renderTable = () => {
    switch (mode) {
      case 'qualification':
        return <Table dataSource={filteredQuals} columns={qualColumns} rowKey="id" loading={loading} size="small" pagination={false} />
      case 'personnel':
        return <Table dataSource={filteredPersonnel} columns={personnelColumns} rowKey="id" loading={loading} size="small" pagination={false} />
      case 'contract':
        return <Table dataSource={filteredContracts} columns={contractColumns} rowKey="id" loading={loading} size="small" pagination={false} />
      case 'history_bid':
        return <Table dataSource={filteredHistoryBids} columns={historyBidColumns} rowKey="id" loading={loading} size="small" pagination={false} />
      case 'company':
        if (!company) return <div style={{ color: '#999', textAlign: 'center', padding: 24 }}>暂无公司信息，请先在资源库中填写</div>
        return (
          <Descriptions column={2} size="small" bordered style={{ marginTop: 8 }}>
            <Descriptions.Item label="公司名称">{company.company_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="统一社会信用代码">{company.business_license_number || '-'}</Descriptions.Item>
            <Descriptions.Item label="法定代表人">{company.legal_rep_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="法定代表人身份证号">{company.legal_rep_id_number || '-'}</Descriptions.Item>
            <Descriptions.Item label="公司地址" span={2}>{company.address || '-'}</Descriptions.Item>
            <Descriptions.Item label="联系电话">{company.contact_phone || '-'}</Descriptions.Item>
            <Descriptions.Item label="网址">{company.website || '-'}</Descriptions.Item>
          </Descriptions>
        )
    }
  }

  return (
    <Modal
      title={`选择资源 — ${requirementName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
      width={mode === 'company' ? 700 : mode === 'contract' || mode === 'personnel' ? 800 : 700}
    >
      <div style={{ marginBottom: 16 }}>
        <Segmented
          value={mode}
          onChange={(v) => { setMode(v as PickerMode); setSearch('') }}
          block
          options={[
            { value: 'qualification', label: '公司资质' },
            { value: 'personnel', label: '人员管理' },
            { value: 'contract', label: '历史合同' },
            { value: 'history_bid', label: '历史投标' },
            { value: 'company', label: '公司信息' },
          ]}
        />
        {mode !== 'company' && (
          <Input
            placeholder={searchPlaceholders[mode]}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            style={{ marginTop: 12 }}
          />
        )}
      </div>
      {renderTable()}
    </Modal>
  )
}
