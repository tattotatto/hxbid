import { useEffect } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import { Button, Space } from 'antd'
import {
  BoldOutlined,
  ItalicOutlined,
  OrderedListOutlined,
  UnorderedListOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'

/**
 * Convert AI-generated plain text to HTML paragraphs for the TipTap editor.
 *
 * The AI outputs content with ``\n\n`` as paragraph separators and ``\n`` as
 * soft line breaks.  TipTap is an HTML editor — feeding it raw plain text
 * causes every line break to collapse into a space, which makes the editor
 * show a single wall of cramped text.
 *
 * Conversely, if the content already looks like HTML (e.g. previously saved
 * by the editor), we return it unchanged.
 */
function plainTextToHTML(text: string): string {
  if (!text) return ''
  // Already HTML (saved from previous editor session)
  if (/<[^>]+>/.test(text)) return text

  return text
    .split(/\n\n+/)          // blank lines → paragraph boundaries
    .map((para) => para.trim())
    .filter(Boolean)
    .map((para) => `<p>${para.replace(/\n/g, '<br>')}</p>`)
    .join('')
}

interface BidEditorProps {
  content: string
  onChange: (html: string) => void
  onSave: () => void
  saving: boolean
}

export default function BidEditor({
  content,
  onChange,
  onSave,
  saving,
}: BidEditorProps): ReactNode {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({
        placeholder: '在此编辑标书内容...',
      }),
    ],
    content: plainTextToHTML(content),
    onUpdate: ({ editor: ed }) => {
      onChange(ed.getHTML())
    },
  })

  // Sync content prop changes into the editor
  useEffect(() => {
    if (editor) {
      const html = plainTextToHTML(content)
      if (html !== editor.getHTML()) {
        editor.commands.setContent(html)
      }
    }
  }, [content, editor])

  if (!editor) {
    return null
  }

  return (
    <div
      style={{
        border: '1px solid #d9d9d9',
        borderRadius: 8,
        minHeight: 400,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          borderBottom: '1px solid #d9d9d9',
          background: '#fafafa',
          borderTopLeftRadius: 7,
          borderTopRightRadius: 7,
        }}
      >
        <Space>
          <Button
            type={editor.isActive('bold') ? 'primary' : 'text'}
            icon={<BoldOutlined />}
            onClick={() => editor.chain().focus().toggleBold().run()}
          />
          <Button
            type={editor.isActive('italic') ? 'primary' : 'text'}
            icon={<ItalicOutlined />}
            onClick={() => editor.chain().focus().toggleItalic().run()}
          />
          <Button
            type={editor.isActive('orderedList') ? 'primary' : 'text'}
            icon={<OrderedListOutlined />}
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
          />
          <Button
            type={editor.isActive('bulletList') ? 'primary' : 'text'}
            icon={<UnorderedListOutlined />}
            onClick={() => editor.chain().focus().toggleBulletList().run()}
          />
        </Space>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={onSave}
        >
          保存
        </Button>
      </div>

      {/* Editor content area */}
      <div style={{ padding: 16, flex: 1 }}>
        <style>{`
          .ProseMirror {
            min-height: 360px;
            outline: none;
            font-size: 15px;
            line-height: 1.8;
            color: #000;
          }
          .ProseMirror p {
            margin: 0 0 8px 0;
            text-indent: 2em;
          }
          .ProseMirror p:last-child {
            margin-bottom: 0;
          }
          .ProseMirror h1 {
            font-size: 18px;
            font-weight: bold;
            margin: 16px 0 8px;
          }
          .ProseMirror h2 {
            font-size: 16px;
            font-weight: bold;
            margin: 14px 0 6px;
          }
        `}</style>
        <EditorContent editor={editor} />
      </div>
    </div>
  )
}
