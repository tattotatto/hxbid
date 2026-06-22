# Task 16: 项目管理页面 (列表 & 创建) — COMPLETE

**Date:** 2026-06-22

## Summary
Replaced the ProjectList and ProjectCreate stub pages with fully functional implementations. ProjectList provides a table view of all projects with CRUD operations. ProjectCreate implements a 2-step wizard for new project creation with bid document upload.

## Files Modified

### `frontend/src/pages/project/ProjectList.tsx`
Full project list page with:
- Header row: "标书项目" title + "新建标书" primary button (PlusOutlined) navigating to `/projects/new`
- Ant Design Table with 5 columns:
  - **项目名称**: Clickable link navigating to `/projects/{id}`
  - **状态**: Tag with color mapping (draft=default, parsed=blue, generating=processing, review=warning, exported=success)
  - **投标截止**: Displays `bid_deadline` or "-" if null
  - **创建时间**: Formatted via `new Date(created_at).toLocaleDateString('zh-CN')`
  - **操作**: "打开" button + Delete Popconfirm (danger, DeleteOutlined)
- Data fetching on mount via `GET /projects/`
- Delete handler: `DELETE /projects/{id}` with success message and refetch
- Loading state on Table during fetch

### `frontend/src/pages/project/ProjectCreate.tsx`
New project creation page with 2-step wizard:
- Card with title "新建标书项目" (maxWidth 700, centered)
- Steps component: Step 0 "填写信息", Step 1 "上传招标文件"

**Step 0 - Form:**
- `name` (required): Input with placeholder "如：XX工业园区2025年度保安服务投标"
- `bid_deadline`: DatePicker full width
- "下一步" submit button: validates form, saves project name, advances to step 1

**Step 1 - Upload:**
- Ant Design `Upload.Dragger`:
  - `accept=".docx,.doc,.pdf,.wps"`
  - `beforeUpload` handler: creates FormData with file + project_name, POSTs to `/bid/upload-and-parse`
  - `showUploadList={false}`, `disabled={uploading}`
  - InboxOutlined icon, drag text, format hint
- On success: success message + navigate to `/projects`
- On error: error message
- "上一步" button (disabled during upload) to return to Step 0

### Other Files (from Tasks 14-15)
- `frontend/src/pages/Login.tsx` — Login page implementation
- `frontend/src/pages/Workbench.tsx` — Workbench page implementation

## Verification
- `npx tsc --noEmit` could not be executed due to tool permission restrictions, but code has been manually reviewed against `tsconfig.json` (strict mode, react-jsx, ESNext modules) and follows the project's established patterns (API client usage, Ant Design component APIs, React Router v6 hooks)
- All imports verified: react-router-dom, antd, @ant-design/icons, API client

## API Endpoints Used
- `GET /api/v1/projects/` — List all projects
- `DELETE /api/v1/projects/{id}` — Delete a project
- `POST /api/v1/bid/upload-and-parse` — Upload and parse bid document (multipart/form-data with `file` and `project_name`)

## Commit
- Commit: `93d889b` on branch `master`
- 4 files changed, 525 insertions(+), 4 deletions(-)
