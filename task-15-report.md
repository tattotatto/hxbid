# Task 15 Report: 登录页 & 工作台

**Date:** 2026-06-22
**Status:** Complete

## Overview

Replaced the Login and Workbench stub pages with full implementations.

## Changes

### 1. `frontend/src/pages/Login.tsx`

Full login page implementation:

- **Layout:** Centered flexbox, `minHeight: 100vh`, gray background `#f0f2f5`
- **Card:** Width 400, containing:
  - Typography.Title (level 2): "宏曦标书"
  - Typography.Text (type="secondary"): "AI 驱动投标书自动生成系统"
  - Form with:
    - username field (UserOutlined prefix, required validation)
    - password field (LockOutlined prefix, required validation)
    - Submit button (type primary, block, loading state)
  - Copyright component at bottom
- **On submit:** POST `/auth/login` via FormData -> store token + user in localStorage -> message.success -> navigate('/')
- **On error:** message.error with `err.response.data.detail` from backend
- **Imports:** `client` from `../api/client`, `Copyright` from `../components/Copyright`

### 2. `frontend/src/pages/Workbench.tsx`

Workbench/home page implementation:

- **Statistics Row (3 Cols, gutter 16):**
  - Card with Statistic "标书总数" + FileTextOutlined
  - Card with Statistic "进行中" + ClockCircleOutlined
  - Card with Statistic "已中标" + CheckCircleOutlined
- **Recently Projects Card:**
  - Extra button: "新建标书" (PlusOutlined, type primary) -> navigate('/projects/new')
  - Table showing last 5 projects (sorted by id desc), columns:
    - 项目名称 (clickable link -> `/projects/:id`)
    - 状态 (Tag with color mapping: draft=default, parsing=processing, parsed=blue, generating=processing, review=warning, exported=success)
    - 中标结果 (Tag with color mapping: 中标=success, 未中标=error, 待定/default)
  - 进行中 count: projects not in ['draft', 'exported']
  - 已中标 count: projects with bid_result === '中标'
- **Loading state:** Spin component while fetching
- **On mount:** fetch projects from GET `/projects/`
- **Imports:** `client` from `../api/client`, `useNavigate` from `react-router-dom`

## Verification

- TypeScript compilation: Cannot verify `npx tsc --noEmit` due to command execution restrictions in current environment. Code patterns are consistent with existing pages (ProjectList.tsx, etc.) that already compile successfully. Run manually:
  ```
  cd frontend && npx tsc --noEmit
  ```

- All imports verified against existing project structure.
- Patterns match existing code: `ColumnsType` from `antd/es/table`, `client` from `../api/client`, `Tag color` props, `message` static API.

## Git

Changes committed in `93d889b` along with Task 16 (ProjectList and ProjectCreate pages).
