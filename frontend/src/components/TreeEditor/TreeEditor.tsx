import React, { useState, useCallback, useEffect } from 'react';
import { message, Spin } from 'antd';
import TreePanel from './TreePanel';
import EditPanel from './EditPanel';
import type { ChapterTreeItem } from './TreePanel';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TreeEditorProps {
  chapters: ChapterTreeItem[];
  projectId: string;
  onContentUpdate?: (chapterId: string, content: string) => void;
  // Legacy save callback — used when chapters lack children_json (old projects)
  onLegacySave?: (chapterId: string, content: string) => Promise<void>;
  legacySaving?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const TreeEditor: React.FC<TreeEditorProps> = ({
  chapters,
  projectId,
  onContentUpdate,
  onLegacySave,
  legacySaving,
}) => {
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedSectionPath, setSelectedSectionPath] = useState<string[]>([]);
  const [currentContent, setCurrentContent] = useState('');
  const [humanEdited, setHumanEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  // Track full chapter content for legacy (markdown-parsed) projects
  const [legacyChapterContents, setLegacyChapterContents] = useState<Record<string, string>>({});

  // Find currently selected chapter
  const selectedChapter = chapters.find((c) => c.id === selectedChapterId);

  // Load content when selection changes
  useEffect(() => {
    if (!selectedChapter) {
      setCurrentContent('');
      setHumanEdited(false);
      return;
    }

    if (selectedSectionPath.length === 0) {
      // 文件型章节 / 根节点：直接展示整章内容（ai_generated_content / final_content）
      setCurrentContent(selectedChapter.content || '');
      setHumanEdited(false);
      return;
    }

    // Find section content from children tree
    // 容器节点读引导段（lead_in），叶子读 content
    const section = findSectionByPath(
      selectedChapter.children || [],
      selectedSectionPath,
    );
    setCurrentContent(section?.lead_in || section?.content || '');
    setHumanEdited(section?.human_edited || false);
  }, [selectedChapterId, selectedSectionPath, chapters]);

  // Handle tree node selection
  const handleSelect = useCallback((chapterId: string, sectionPath: string[]) => {
    setSelectedChapterId(chapterId);
    setSelectedSectionPath(sectionPath);
  }, []);

  // Handle content change (from editor)
  const handleContentChange = useCallback((content: string) => {
    setCurrentContent(content);
    setHumanEdited(true);
  }, []);

  // Save section content
  const handleSave = useCallback(async () => {
    if (!selectedChapterId) return;
    setSaving(true);
    try {
      const chapter = chapters.find(c => c.id === selectedChapterId);
      const token = localStorage.getItem('token') || '';

      // 编辑章节内小节：先写回 children_json（容器写 lead_in / 叶子写 content），
      // 保证新管线（children_json 为目录树来源）下编辑在刷新后不丢失。
      if (selectedSectionPath.length > 0 && (chapter?.children?.length ?? 0) > 0) {
        const secRes = await fetch(
          `/api/v1/projects/${projectId}/chapters/${selectedChapterId}/sections/save`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
              section_path: selectedSectionPath,
              content: currentContent,
            }),
          },
        );
        if (!secRes.ok) throw new Error('保存小节失败');
      }

      // 重建整章内容后写回 final_content（渲染/导出使用）：
      // - 编辑章节内小节：替换该小节内容后重建整章 markdown；
      // - 编辑整章（文件型 / 旧项目 markdown 解析章节）：children 为空时直接取当前内容。
      const fullContent = rebuildFullContent(
        chapter?.children || [],
        selectedSectionPath,
        currentContent,
      );

      const res = await fetch(`/api/v1/projects/${projectId}/chapters/${selectedChapterId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          final_content: fullContent,
        }),
      });
      if (!res.ok) throw new Error('保存失败');
      message.success('保存成功');
      if (onContentUpdate) {
        onContentUpdate(selectedChapterId, fullContent);
      }
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setSaving(false);
    }
  }, [selectedChapterId, selectedSectionPath, currentContent, projectId, chapters, onContentUpdate]);

  // AI modify section
  const handleAIModify = useCallback(async (instruction: string) => {
    const token = localStorage.getItem('token') || '';
    const res = await fetch(
      `/api/v1/bid/${projectId}/chapters/${selectedChapterId}/sections/modify`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          section_path: selectedSectionPath,
          current_content: currentContent,
          instruction,
        }),
      },
    );
    if (!res.ok) throw new Error('AI 修改失败');
    return res.json();
  }, [projectId, selectedChapterId, selectedSectionPath, currentContent]);

  // Regenerate section
  const handleRegenerate = useCallback(async () => {
    const token = localStorage.getItem('token') || '';
    const res = await fetch(
      `/api/v1/bid/${projectId}/chapters/${selectedChapterId}/sections/regenerate`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          section_path: selectedSectionPath,
          token_budget_hint: 'medium',
        }),
      },
    );
    if (!res.ok) throw new Error('重新生成失败');

    // Stream SSE response
    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let buffer = '';
    let fullContent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.text) {
              fullContent += data.text;
              setCurrentContent(fullContent);
            }
          } catch {}
        }
      }
    }
  }, [projectId, selectedChapterId, selectedSectionPath]);

  if (chapters.length === 0) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: '#8c8c8c',
      }}>
        <Spin tip="加载章节..." />
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      height: '100%',
      minHeight: 500,
      border: '1px solid #f0f0f0',
      borderRadius: 6,
      overflow: 'hidden',
    }}>
      <TreePanel
        chapters={chapters}
        selectedPath={selectedSectionPath.length > 0
          ? [selectedChapterId || '', ...selectedSectionPath]
          : (selectedChapterId ? [selectedChapterId] : null)
        }
        onSelect={handleSelect}
      />
      <EditPanel
        chapterId={selectedChapterId}
        chapterTitle={selectedChapter?.title || ''}
        sectionPath={selectedSectionPath}
        sectionTitle={selectedSectionPath[selectedSectionPath.length - 1] || selectedChapter?.title || ''}
        content={currentContent}
        humanEdited={humanEdited}
        isFileType={selectedChapter?.chapter_type !== 'ai_generated'}
        saving={saving}
        onContentChange={handleContentChange}
        onSave={handleSave}
        onAIModify={handleAIModify}
        onRegenerate={handleRegenerate}
      />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// 依据节点层级生成 markdown 标题（# ~ ######）
// - 嵌套树（children_json v2）节点带 depth：depth 1 → ##、depth 2 → ###、...
// - 旧 markdown 解析树节点带 level：level 1 → #、level 2 → ##、...
function headingOf(node: any): string {
  let level: number;
  if (node.depth != null) {
    level = node.depth + 1;
  } else {
    level = node.level || 2;
  }
  level = Math.min(Math.max(level, 1), 6);
  return '#'.repeat(level) + ' ' + node.title;
}

// Rebuild full markdown content from tree structure after editing a section.
// 规则：
// - 容器节点输出 heading + lead_in（兜底 content），并递归其子内容；
// - 叶子节点输出 heading + content；
// - 无正文（无 lead_in / content）的节点绝不输出裸标题（杜绝"空标题"）。
function rebuildFullContent(
  children: any[],
  sectionPath: string[],
  newContent: string,
): string {
  if (!children || children.length === 0) return newContent;

  const parts: string[] = [];
  for (const node of children) {
    const hasChildren = !!(node.children && node.children.length > 0);
    const isTarget = node.title === sectionPath[0];

    if (isTarget && sectionPath.length === 1) {
      // Target is right here — edited content replaces lead_in (容器) / content (叶子)
      const body = newContent.trim() ? newContent : '';
      if (body) {
        parts.push(`${headingOf(node)}\n\n${body}`);
      }
      if (hasChildren) {
        const childContent = rebuildFullContent(node.children, [], '');
        if (childContent) parts.push(childContent);
      }
    } else if (isTarget && hasChildren) {
      // Go deeper
      parts.push(`${headingOf(node)}\n\n${rebuildFullContent(node.children, sectionPath.slice(1), newContent)}`);
    } else {
      // Not the target — keep original content (lead_in for containers, content for leaves)
      const body = (node.lead_in || node.content || '').trim();
      if (body) {
        parts.push(`${headingOf(node)}\n\n${body}`);
      }
      if (hasChildren) {
        const childContent = rebuildFullContent(node.children, [], '');
        if (childContent) parts.push(childContent);
      }
    }
  }
  return parts.join('\n\n');
}

function findSectionByPath(
  children: any[],
  path: string[],
): any | null {
  if (!children || children.length === 0) return null;
  for (const node of children) {
    if (node.title === path[0]) {
      if (path.length === 1) return node;
      return findSectionByPath(node.children || [], path.slice(1));
    }
  }
  return null;
}

export default TreeEditor;
