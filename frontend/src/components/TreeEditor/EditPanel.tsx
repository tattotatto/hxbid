import React, { useState, useCallback } from 'react';
import {
  Button, Space, Breadcrumb, Tag, Input, Modal, message, Spin, Typography,
} from 'antd';
import {
  SaveOutlined,
  RobotOutlined,
  ReloadOutlined,
  EditOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import BidEditor from '../BidEditor';

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EditPanelProps {
  chapterId: string | null;
  chapterTitle: string;
  sectionPath: string[];
  sectionTitle: string;
  content: string;
  humanEdited: boolean;
  isFileType: boolean;
  saving: boolean;
  onContentChange: (content: string) => void;
  onSave: () => void;
  onAIModify: (instruction: string) => Promise<{ modified_content: string; diff_summary: string }>;
  onRegenerate: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const EditPanel: React.FC<EditPanelProps> = ({
  chapterId,
  chapterTitle,
  sectionPath,
  sectionTitle,
  content,
  humanEdited,
  isFileType,
  saving,
  onContentChange,
  onSave,
  onAIModify,
  onRegenerate,
}) => {
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResult, setAiResult] = useState<{ modified_content: string; diff_summary: string } | null>(null);
  const [diffModalOpen, setDiffModalOpen] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  // Save handler
  const handleSave = useCallback(() => {
    onSave();
  }, [onSave]);

  // AI Modify
  const handleAIModify = useCallback(async () => {
    if (!aiInstruction.trim()) return;
    setAiLoading(true);
    try {
      const result = await onAIModify(aiInstruction);
      setAiResult(result);
      setDiffModalOpen(true);
    } catch (err: any) {
      message.error(err?.message || 'AI 修改失败');
    } finally {
      setAiLoading(false);
    }
  }, [aiInstruction, onAIModify]);

  // Accept AI modification
  const handleAcceptAI = useCallback(() => {
    if (aiResult) {
      onContentChange(aiResult.modified_content);
      setDiffModalOpen(false);
      setAiResult(null);
      setAiOpen(false);
      setAiInstruction('');
      message.success('已应用 AI 修改');
    }
  }, [aiResult, onContentChange]);

  // Regenerate
  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    try {
      await onRegenerate();
      message.success('重新生成完成');
    } catch (err: any) {
      message.error(err?.message || '重新生成失败');
    } finally {
      setRegenerating(false);
    }
  }, [onRegenerate]);

  if (!chapterId) {
    return (
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#8c8c8c',
        fontSize: 14,
      }}>
        请从左侧目录选择要编辑的章节
      </div>
    );
  }

  const breadcrumbItems = [
    { title: chapterTitle },
    ...sectionPath.map((p) => ({ title: p })),
  ];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Toolbar */}
      <div style={{
        padding: '8px 16px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 8,
      }}>
        <Space>
          <Breadcrumb items={breadcrumbItems} style={{ fontSize: 12 }} />
          {humanEdited && <Tag color="warning" icon={<EditOutlined />}>已修改</Tag>}
          {content && !humanEdited && <Tag color="success" icon={<CheckCircleOutlined />}>已生成</Tag>}
        </Space>

        <Space>
          {!isFileType && (
            <>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={regenerating}
                onClick={handleRegenerate}
              >
                重新生成
              </Button>
              <Button
                size="small"
                type="primary"
                icon={<RobotOutlined />}
                onClick={() => setAiOpen(!aiOpen)}
              >
                AI 修改
              </Button>
            </>
          )}
          <Button
            size="small"
            icon={<SaveOutlined />}
            type="primary"
            loading={saving}
            onClick={handleSave}
          >
            保存
          </Button>
        </Space>
      </div>

      {/* AI Chat Panel */}
      {aiOpen && (
        <div style={{
          padding: '12px 16px',
          borderBottom: '1px solid #f0f0f0',
          background: '#f6f8fa',
        }}>
          <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
            🤖 将对【{sectionPath[sectionPath.length - 1] || sectionTitle}】进行针对性修改
          </Text>
          <Input.TextArea
            rows={2}
            placeholder="输入修改要求，例如：需要明确24小时三班倒的具体排班方式"
            value={aiInstruction}
            onChange={(e) => setAiInstruction(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <Button
            type="primary"
            size="small"
            icon={<RobotOutlined />}
            loading={aiLoading}
            onClick={handleAIModify}
            disabled={!aiInstruction.trim()}
          >
            发送修改要求
          </Button>
        </div>
      )}

      {/* Editor */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {regenerating ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
          }}>
            <Spin tip="正在重新生成..." />
          </div>
        ) : (
          <BidEditor
            content={content}
            onChange={onContentChange}
            onSave={handleSave}
            saving={saving}
          />
        )}
      </div>

      {/* Diff Modal */}
      <Modal
        title="AI 修改预览"
        open={diffModalOpen}
        onOk={handleAcceptAI}
        onCancel={() => {
          setDiffModalOpen(false);
          setAiResult(null);
        }}
        okText="接受修改"
        cancelText="继续调整"
        width={900}
      >
        {aiResult && (
          <div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
              {aiResult.diff_summary}
            </Text>
            <div style={{ display: 'flex', gap: 12 }}>
              {/* Before */}
              <div style={{ flex: 1 }}>
                <Text strong style={{ fontSize: 11, color: '#8c8c8c' }}>原文</Text>
                <div style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 4,
                  padding: 8,
                  maxHeight: 400,
                  overflow: 'auto',
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                  background: '#fafafa',
                }}>
                  {content}
                </div>
              </div>
              {/* After */}
              <div style={{ flex: 1 }}>
                <Text strong style={{ fontSize: 11, color: '#1677ff' }}>AI 修改</Text>
                <div style={{
                  border: '1px solid #91caff',
                  borderRadius: 4,
                  padding: 8,
                  maxHeight: 400,
                  overflow: 'auto',
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                  background: '#e6f4ff',
                }}>
                  {aiResult.modified_content}
                </div>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default EditPanel;
