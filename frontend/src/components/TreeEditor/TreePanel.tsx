import React, { useMemo, useState } from 'react';
import { Tree, Input, Tag, Space } from 'antd';
import {
  FileTextOutlined,
  FolderOutlined,
  CheckCircleOutlined,
  EditOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SectionNode {
  title: string;
  content?: string;
  children?: SectionNode[];
  token_budget_hint?: string;
  ai_generated?: boolean;
  human_edited?: boolean;
  // Flattened task format
  path?: string[];
  status?: string; // 'pending' | 'generating' | 'done' | 'failed'
}

export interface ChapterTreeItem {
  id: string;
  title: string;
  order_index: number;
  chapter_type: string; // 'fixed_form' | 'table' | 'ai_generated' | 'attachment'
  review_status: string;
  status: string;
  children?: SectionNode[];
}

interface TreePanelProps {
  chapters: ChapterTreeItem[];
  selectedPath: string[] | null; // [chapterTitle?, ...sectionPath]
  onSelect: (chapterId: string, sectionPath: string[]) => void;
  collapsed?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildTreeData(
  chapters: ChapterTreeItem[],
  selectedPath: string[] | null,
): DataNode[] {
  return chapters.map((ch) => {
    const isFileType = ch.chapter_type !== 'ai_generated';
    const children = ch.children || [];

    // Determine icon and status tag
    let icon: React.ReactNode;
    let statusTag: React.ReactNode | null = null;

    if (isFileType) {
      icon = <FileTextOutlined style={{ color: '#8c8c8c' }} />;
      statusTag = ch.status === 'generated'
        ? <Tag color="success" style={{ fontSize: 10, lineHeight: '16px' }}>✓</Tag>
        : null;
    } else if (children.length > 0) {
      icon = <FolderOutlined style={{ color: '#1677ff' }} />;
    } else {
      icon = <FileTextOutlined style={{ color: '#1677ff' }} />;
    }

    // Build tree node
    const node: DataNode = {
      key: ch.id,
      title: (
        <Space size={4}>
          {icon}
          <span style={{ fontSize: 13 }}>{ch.title}</span>
          {statusTag}
        </Space>
      ),
      isLeaf: isFileType || children.length === 0,
      selectable: isFileType || children.length === 0,
    };

    // Add sub-nodes for ai_generated chapters
    if (!isFileType && children.length > 0) {
      node.children = children.map((section, si) =>
        buildSectionNode(ch.id, section, [ch.title], si, selectedPath),
      );
    }

    return node;
  });
}

function buildSectionNode(
  chapterId: string,
  section: SectionNode,
  parentPath: string[],
  index: number,
  selectedPath: string[] | null,
): DataNode {
  const currentPath = [...parentPath, section.title];
  const hasChildren = section.children && section.children.length > 0;
  const isSelected = selectedPath &&
    selectedPath.length === currentPath.length &&
    selectedPath.every((p, i) => p === currentPath[i]);

  // Status icon
  let statusIcon: React.ReactNode = null;
  if (section.human_edited) {
    statusIcon = <EditOutlined style={{ color: '#faad14', fontSize: 11 }} />;
  } else if (section.content) {
    statusIcon = <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 11 }} />;
  } else if (section.status === 'generating') {
    statusIcon = <ClockCircleOutlined style={{ color: '#1677ff', fontSize: 11 }} />;
  } else if (section.status === 'failed') {
    statusIcon = <ExclamationCircleOutlined style={{ color: '#ff4d4f', fontSize: 11 }} />;
  }

  const node: DataNode = {
    key: `${chapterId}::${currentPath.join('::')}`,
    title: (
      <Space size={4}>
        {hasChildren
          ? <FolderOutlined style={{ color: '#1677ff', fontSize: 12 }} />
          : <FileTextOutlined style={{ color: '#595959', fontSize: 12 }} />
        }
        <span style={{
          fontSize: 12,
          fontWeight: isSelected ? 600 : 400,
          color: isSelected ? '#1677ff' : undefined,
        }}>
          {section.title}
        </span>
        {statusIcon}
      </Space>
    ),
    isLeaf: !hasChildren,
    selectable: !hasChildren, // Only leaf nodes are selectable
  };

  if (hasChildren && section.children) {
    node.children = section.children.map((child, ci) =>
      buildSectionNode(chapterId, child, currentPath, ci, selectedPath),
    );
  }

  return node;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const TreePanel: React.FC<TreePanelProps> = ({
  chapters,
  selectedPath,
  onSelect,
  collapsed,
}) => {
  const [search, setSearch] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);

  const treeData = useMemo(
    () => buildTreeData(chapters, selectedPath),
    [chapters, selectedPath],
  );

  // Auto-expand nodes matching search
  const filteredTree = useMemo(() => {
    if (!search.trim()) return treeData;

    const filter = (nodes: DataNode[]): DataNode[] =>
      nodes
        .map((node) => {
          const titleStr = typeof node.title === 'string' ? node.title : '';
          const children = node.children ? filter(node.children as DataNode[]) : [];
          const matchesSearch = titleStr.toLowerCase().includes(search.toLowerCase());
          const hasMatchingChild = children.length > 0;

          if (matchesSearch || hasMatchingChild) {
            return { ...node, children: children.length > 0 ? children : node.children };
          }
          return null;
        })
        .filter(Boolean) as DataNode[];

    return filter(treeData);
  }, [treeData, search]);

  // Expand all matching nodes when searching
  React.useEffect(() => {
    if (search.trim()) {
      const keys: string[] = [];
      const collect = (nodes: DataNode[]) => {
        for (const n of nodes) {
          if (n.children && n.children.length > 0) {
            keys.push(n.key as string);
            collect(n.children as DataNode[]);
          }
        }
      };
      collect(filteredTree);
      setExpandedKeys(keys);
    }
  }, [search, filteredTree]);

  if (collapsed) return null;

  return (
    <div style={{
      width: 300,
      minWidth: 300,
      borderRight: '1px solid #f0f0f0',
      display: 'flex',
      flexDirection: 'column',
      background: '#fafafa',
    }}>
      {/* Search */}
      <div style={{ padding: '8px 12px' }}>
        <Input
          prefix={<SearchOutlined />}
          placeholder="搜索章节..."
          size="small"
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Tree */}
      <div style={{ flex: 1, overflow: 'auto', padding: '4px 8px' }}>
        <Tree
          showIcon={false}
          treeData={filteredTree}
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys as string[])}
          onSelect={(keys) => {
            if (keys.length === 0) return;
            const key = keys[0] as string;

            // Parse key: "chapterId" or "chapterId::section1::section2"
            if (key.includes('::')) {
              const [chapterId, ...sectionPath] = key.split('::');
              onSelect(chapterId, sectionPath);
            } else {
              onSelect(key, []);
            }
          }}
          selectedKeys={selectedPath ? [selectedPath.join('::')] : []}
          style={{ fontSize: 12 }}
        />
      </div>

      {/* Legend */}
      <div style={{
        padding: '8px 12px',
        borderTop: '1px solid #f0f0f0',
        background: '#fff',
      }}>
        <Space size={12} style={{ fontSize: 11, color: '#8c8c8c' }}>
          <span><CheckCircleOutlined style={{ color: '#52c41a' }} /> 已生成</span>
          <span><EditOutlined style={{ color: '#faad14' }} /> 已修改</span>
          <span><ClockCircleOutlined style={{ color: '#1677ff' }} /> 生成中</span>
        </Space>
      </div>
    </div>
  );
};

export default TreePanel;
