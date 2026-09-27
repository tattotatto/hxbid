import React, { useMemo, useState } from 'react'
import { Tree, Button, Space, Tag, Input, Empty, Tooltip, Modal, Select, message } from 'antd'
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
import { USER_SELECTABLE_TYPES } from '../../api/outline'
import {
  flatten,
  getNode,
  insertNode,
  moveNode,
  nodeBadge,
  removeNode,
  updateNode,
} from './outlineTreeOps'

interface OutlineTreeProps {
  chapters: OutlineChapter[]
  onChange: (next: OutlineChapter[]) => void
  /** 标题或类型变化时通知父组件重新匹配（父组件负责防抖与落库） */
  onMatchRequest?: (path: number[], title: string, type: ChapterType) => void
  /** 点匹配徽标时通知父组件打开抽屉 */
  onOpenMatch?: (path: number[]) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function buildTreeData(
  chapters: OutlineChapter[],
  basePath: number[],
  onTypeChange: (path: number[], type: ChapterType) => void,
  onOpenMatch: (path: number[]) => void,
): DataNode[] {
  return chapters.map((ch, idx) => {
    const path = [...basePath, idx]
    // 兼容后端两种返回形态：
    // 1. chapter_structure_json（extract/chat 输出） → 字段名 type
    // 2. ProjectChapter 行 → 字段名 chapter_type
    // 历史上前端只用 type，导致已 confirm 的项目回看 /outline 页面渲染「暂无章节」。
    const type = (ch.type ?? (ch as any).chapter_type ?? 'ai_generated') as ChapterType
    // 徽标也是打开抽屉的唯一入口 —— 附件类必须有（见 nodeBadge 的说明）
    const badge = nodeBadge(ch)

    return {
      key: path.join('.'),
      title: (
        <Space size={4}>
          <Select
            size="small"
            value={type}
            style={{ width: 104 }}
            onClick={(e) => e.stopPropagation()}
            onChange={(v) => onTypeChange(path, v as ChapterType)}
            options={USER_SELECTABLE_TYPES.map((t) => ({
              value: t,
              label: TYPE_LABELS[t],
            }))}
          />
          <span style={{ fontWeight: 500 }}>{ch.title || '(未命名)'}</span>
          {ch.source === 'scoring_rubric' && (
            <Tag color="gold" style={{ marginRight: 0 }}>来自评标办法</Tag>
          )}
          {badge && (
            <Tooltip title={badge.hint}>
              <Tag
                color={badge.tone === 'attachment' ? 'orange' : badge.tone}
                style={{ marginRight: 0, cursor: 'pointer' }}
                onClick={(e) => {
                  e.stopPropagation()
                  onOpenMatch(path)
                }}
              >
                {badge.text}
              </Tag>
            </Tooltip>
          )}
        </Space>
      ),
      children: ch.children
        ? buildTreeData(ch.children, path, onTypeChange, onOpenMatch)
        : undefined,
    }
  })
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const OutlineTree: React.FC<OutlineTreeProps> = ({
  chapters,
  onChange,
  onMatchRequest,
  onOpenMatch,
}) => {
  const [selectedPath, setSelectedPath] = useState<number[] | null>(null)
  const [editingTitle, setEditingTitle] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')

  const handleTypeChange = (path: number[], type: ChapterType) => {
    const next = updateNode(chapters, path, (n) => ({ ...n, type }))
    onChange(next)
    const node = getNode(next, path)
    if (node) onMatchRequest?.(path, node.title, type)
  }

  const treeData = useMemo(
    () =>
      buildTreeData(
        chapters,
        [],
        handleTypeChange,
        (p) => onOpenMatch?.(p),
      ),
    // handleTypeChange 每次渲染都是新函数，但它只闭包 chapters / onChange /
    // onMatchRequest，三者在依赖里都已列出
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chapters, onChange, onMatchRequest, onOpenMatch],
  )
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
    if (next === chapters) {
      // 静默忽略会让人以为拖拽坏了
      if (dragPath.join('.') !== dropPath.join('.')) {
        message.warning('不能把章节拖进它自己的子章节')
      }
      return
    }
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
      content: `确定要删除「${selectedNode?.title ?? ''}」及其所有子章节？删除会自动保存到服务器。`,
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
    const next = updateNode(chapters, selectedPath, (n) => ({
      ...n,
      title: titleDraft.trim() || n.title,
    }))
    onChange(next)
    const node = getNode(next, selectedPath)
    if (node) {
      onMatchRequest?.(
        selectedPath,
        node.title,
        (node.type ?? 'ai_generated') as ChapterType,
      )
    }
    setEditingTitle(null)
  }

  const handleReset = () => {
    Modal.confirm({
      title: '重新加载服务端版本',
      content: '将丢弃尚未保存的改名/增删/匹配/附件改动，从服务器重新拉取章节结构。已保存的改动不受影响。',
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
