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
      // File type chapter or root ai_generated → load ai_generated_content
      // For now, we use content from children_json
      setCurrentContent('');
      setHumanEdited(false);
      return;
    }

    // Find section content from children tree
    const section = findSectionByPath(
      selectedChapter.children || [],
      selectedSectionPath,
    );
    setCurrentContent(section?.content || '');
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
      // Check if this chapter has structured children_json (new pipeline)
      const chapter = chapters.find(c => c.id === selectedChapterId);
      const hasStructuredChildren = chapter?.children && chapter.children.length > 0
        && !('content' in chapter.children[0]) === false; // has children_json with content fields

      if (selectedSectionPath.length > 0) {
        // Legacy mode: save by reconstructing full chapter content
        const token = localStorage.getItem('token') || '';

        // Build updated full content by walking the tree and replacing this section
        const sectionTitle = selectedSectionPath[selectedSectionPath.length - 1];
        const fullContent = rebuildFullContent(
          chapter?.children || [],
          selectedSectionPath,
          currentContent,
        );

        // Save full chapter via project chapters API
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
      } else {
        // New pipeline mode: save single section
        const token = localStorage.getItem('token') || '';
        const res = await fetch(`/api/v1/bid/${projectId}/chapters/${selectedChapterId}/sections/save`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            section_path: selectedSectionPath,
            content: currentContent,
          }),
        });
        if (!res.ok) throw new Error('保存失败');
        message.success('保存成功');
        if (onContentUpdate) {
          onContentUpdate(selectedChapterId, currentContent);
        }
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

// Rebuild full markdown content from tree structure after editing a section
function rebuildFullContent(
  children: any[],
  sectionPath: string[],
  newContent: string,
): string {
  if (!children || children.length === 0) return newContent;

  const parts: string[] = [];
  for (const node of children) {
    const isTarget = node.title === sectionPath[0];
    if (isTarget && sectionPath.length === 1) {
      // This is the target section — replace its content
      parts.push(`## ${node.title}\n\n${newContent}`);
    } else if (isTarget && node.children && node.children.length > 0) {
      // Go deeper
      parts.push(`## ${node.title}\n\n${rebuildFullContent(node.children, sectionPath.slice(1), newContent)}`);
    } else {
      // Not the target — keep original content
      const content = node.content || '';
      if (content) {
        parts.push(`## ${node.title}\n\n${content}`);
      } else {
        parts.push(`## ${node.title}`);
        if (node.children && node.children.length > 0) {
          parts.push(rebuildFullContent(node.children, [], ''));
        }
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
