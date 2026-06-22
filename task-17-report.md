# Task 17: 标书项目核心流程页 + 编辑器

## Overview
Implement the main workflow page, generation progress component, and rich text editor.

## Components Created/Replaced

### 1. GenerationProgress.tsx
- Progress bar with Ant Design
- Tag list showing each chapter status

### 2. BidEditor.tsx
- Rich text editor using @tiptap/react
- Toolbar with bold, italic, list buttons
- Save button integration

### 3. ProjectWorkflow.tsx (replaced)
- 5-step workflow: Upload -> Parse -> Generate -> Edit -> Export
- SSE generation handling with ReadableStream
- Tab-based chapter editor
- Save and export functionality
