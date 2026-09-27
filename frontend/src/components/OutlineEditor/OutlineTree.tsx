import React, { useMemo, useState } from 'react'
import { Tree, Button, Space, Tag, Input, Empty, Tooltip, Modal } from 'antd'
import {
  PlusOutlined,
  MinusOutlined,
  EditOutlined,
  CheckOutlined,
  CloseOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { DataNode, TreeProps } from 'antd/es/tree'
import type { OutlineChapter, ChapterType } from '../../api/outline'
import {
  flatten,
  getNode,
  insertNode,
  moveNode,
  removeNode,
  updateNode,
} from './outlineTreeOps'

interface OutlineTreeProps {
  chapters: OutlineChapter[]
  onChange: (next: OutlineChapter[]) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TYPE_COLORS: Record<ChapterType, string> = {
  fixed_form: 'blue',
  table: 'purple',
  ai_generated: 'green',
  attachment: 'orange',
  mixed: 'magenta',
}

const TYPE_LABELS: Record<ChapterType, string> = {
  fixed_form: '固定格式',
  table: '表格',
  ai_generated: 'AI 生成',
  attachment: '附件',
  mixed: '混合',
}

let nextId = 0
const tempId = () => `__new_${Date.now()}_${nextId++}`

function makeNewChapter(orderIndex: number): OutlineChapter {
  return {
    order_index: orderIndex,
    number: '',
    title: '新章节',
    type: 'ai_generated',
    required: false,
    children: [],
  }
}

function buildTreeData(chapters: OutlineChapter[]): DataNode[] {
  return chapters.map((ch, idx) => {
    // 兼容后端两种返回形态：
    // 1. chapter_structure_json（extract/chat 输出） → 字段名 type
    // 2. ProjectChapter 行 → 字段名 chapter_type
    // 历史上前端只用 type，导致已 confirm 的项目回看 /outline 页面渲染「暂无章节」。
    const type = (ch.type ?? (ch as any).chapter_type ?? 'ai_generated') as ChapterType
    return {
      key: String(idx),
      title: (
        <Space size={4}>
          <Tag color={TYPE_COLORS[type] ?? 'default'} style={{ marginRight: 0 }}>
            {TYPE_LABELS[type] ?? type}
          </Tag>
          <span style={{ fontWeight: 500 }}>{ch.title || '(未命名)'}</span>
          {ch.source === 'scoring_rubric' && (
            <Tag color="gold" style={{ marginRight: 0, marginLeft: 6 }}>来自评标办法</Tag>
          )}
        </Space>
      ),
      children: ch.children ? buildTreeData(ch.children) : undefined,
    }
  })
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const OutlineTree: React.FC<OutlineTreeProps> = ({ chapters, onChange }) => {
  const [selectedPath, setSelectedPath] = useState<number[] | null>(null)
  const [editingTitle, setEditingTitle] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')

  const treeData = useMemo(() => buildTreeData(chapters), [chapters])
  const flatList = useMemo(() => flatten(chapters, null, []), [chapters])

  const selectedNode =
    selectedPath && selectedPath.length > 0 ? getNode(chapters, selectedPath) : null

  const selectedKey = selectedPath ? selectedPath.join('.') : null

  const handleSelect = (keys: React.Key[]) => {
    if (keys.length === 0) {
      setSelectedPath(null)
      return
    }
    const key = String(keys[0])
    setSelectedPath(key.split('.').map((s) => Number(s)))
  }

  const handleDrop: TreeProps['onDrop'] = (info) => {
    const dragPath = String(info.dragNode.key).split('.').map(Number)
    const dropPath = String(info.node.key).split('.').map(Number)
    // antd 的 dropPosition 是相对投影值：-1 之前、0 之内、1 之后
    const dropPos = info.node.pos.split('-')
    const dropOffset = info.dropPosition - Number(dropPos[dropPos.length - 1])

    const position: 'before' | 'after' | 'inside' =
      info.dropToGap ? (dropOffset < 0 ? 'before' : 'after') : 'inside'

    const next = moveNode(chapters, dragPath, dropPath, position)
    if (next === chapters) return
    onChange(next)
  }

  const handleAdd = (position: 'top' | 'child') => {
    const parentPath =
      position === 'child' && selectedPath && selectedPath.length > 0
        ? selectedPath
        : []
    const baseOrder = chapters.length + 1
    const next = insertNode(chapters, parentPath, 'child', makeNewChapter(baseOrder))
    onChange(next)
    // 自动选中新节点
    if (parentPath.length === 0) {
      setSelectedPath([next.length - 1])
    } else {
      setSelectedPath([...parentPath, (next[parentPath[0]]?.children?.length ?? 1) - 1])
    }
  }

  const handleDelete = () => {
    if (!selectedPath || selectedPath.length === 0) return
    Modal.confirm({
      title: '删除选中章节',
      content: `确定要删除「${selectedNode?.title ?? ''}」及其所有子章节？此操作不会上传到服务器。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => {
        onChange(removeNode(chapters, selectedPath))
        setSelectedPath(null)
      },
    })
  }

  const startEditTitle = () => {
    if (!selectedNode) return
    setEditingTitle(selectedPath!.join('.'))
    setTitleDraft(selectedNode.title)
  }

  const commitTitle = () => {
    if (!editingTitle || !selectedPath) {
      setEditingTitle(null)
      return
    }
    onChange(updateNode(chapters, selectedPath, (n) => ({ ...n, title: titleDraft.trim() || n.title })))
    setEditingTitle(null)
  }

  const handleReset = () => {
    Modal.confirm({
      title: '放弃本地修改',
      content: '本地手动编辑未上传到服务器，确认重新加载服务端版本？（仅丢失手动改名/增删，AI 对话修改仍保留）',
      okText: '重新加载',
      cancelText: '取消',
      onOk: () => window.location.reload(),
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Space style={{ marginBottom: 8 }} wrap>
        <Tooltip title="新增顶级章节">
          <Button icon={<PlusOutlined />} size="small" onClick={() => handleAdd('top')}>
            新增顶级
          </Button>
        </Tooltip>
        <Tooltip title="在选中节点下新增子章节">
          <Button
            icon={<PlusOutlined />}
            size="small"
            disabled={!selectedPath}
            onClick={() => handleAdd('child')}
          >
            新增子节
          </Button>
        </Tooltip>
        <Tooltip title="编辑选中章节标题">
          <Button
            icon={<EditOutlined />}
            size="small"
            disabled={!selectedNode}
            onClick={startEditTitle}
          >
            重命名
          </Button>
        </Tooltip>
        <Tooltip title="删除选中章节">
          <Button
            icon={<MinusOutlined />}
            size="small"
            danger
            disabled={!selectedNode}
            onClick={handleDelete}
          >
            删除
          </Button>
        </Tooltip>
        <Tooltip title="重新加载服务器版本（放弃本地手动修改）">
          <Button icon={<ReloadOutlined />} size="small" onClick={handleReset}>
            重新加载
          </Button>
        </Tooltip>
      </Space>

      {selectedNode && editingTitle === selectedPath?.join('.') ? (
        <Space.Compact style={{ marginBottom: 8, width: '100%' }}>
          <Input
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onPressEnter={commitTitle}
            autoFocus
            placeholder="新标题"
          />
          <Button icon={<CheckOutlined />} type="primary" onClick={commitTitle} />
          <Button icon={<CloseOutlined />} onClick={() => setEditingTitle(null)} />
        </Space.Compact>
      ) : null}

      <div style={{ flex: 1, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, background: '#fafafa' }}>
        {treeData.length === 0 ? (
          <Empty description="暂无章节" />
        ) : (
          <Tree
            treeData={treeData}
            defaultExpandAll
            selectedKeys={selectedKey ? [selectedKey] : []}
            onSelect={handleSelect}
            draggable
            onDrop={handleDrop}
            blockNode
            showLine
          />
        )}
      </div>
      <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
        共 {chapters.length} 个顶级章节 / {flatList.length} 个节点（含子章节）。拖拽可调整顺序与层级，修改会自动保存。
      </div>
    </div>
  )
}

export default OutlineTree
