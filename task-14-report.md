# Task 14: 前端脚手架 & 布局组件 — COMPLETE

**Date:** 2026-06-22

## Summary
Created the frontend scaffold for 宏曦标书 MVP: Vite + React + TypeScript + Ant Design, with a ProLayout-style layout, routing, API client, and stub pages for all routes.

## Files Created (18 source files)

### Config
- `frontend/package.json` — Dependencies: React 18, React Router 6, Ant Design 5, TipTap, Axios, Dayjs; Dev: Vite 5, TypeScript 5
- `frontend/vite.config.ts` — Vite config with React plugin, dev port 5173, API proxy to localhost:8000
- `frontend/tsconfig.json` — Strict TypeScript, react-jsx, ESNext modules, bundler resolution
- `frontend/index.html` — Entry HTML with zh-CN lang, "宏曦标书" title

### Core
- `frontend/src/main.tsx` — React 18 root with StrictMode, Ant Design ConfigProvider (zh_CN locale, blue primary color), BrowserRouter
- `frontend/src/App.tsx` — Route definitions: /login (public), / (layout) with nested routes for workbench, projects, resources, settings; auth guard redirects unauthenticated users to /login

### API
- `frontend/src/api/client.ts` — Axios instance with /api/v1 base URL, Bearer token injection, 401 auto-redirect to login

### Components
- `frontend/src/components/Layout.tsx` — Ant Design Sider+Header+Content+Footer layout:
  - Sider: collapsible, "宏曦标书" / "宏曦" logo, navigation menu (工作台, 标书项目, 资源库 submenu with 公司资质/人员信息/历史标书, 系统设置)
  - Header: "退出" logout button
  - Content: React Router `<Outlet />`
  - Footer: Copyright component
- `frontend/src/components/Copyright.tsx` — Copyright with current year

### Stub Pages (9)
- `frontend/src/pages/Login.tsx`
- `frontend/src/pages/Workbench.tsx`
- `frontend/src/pages/project/ProjectList.tsx`
- `frontend/src/pages/project/ProjectCreate.tsx`
- `frontend/src/pages/project/ProjectWorkflow.tsx`
- `frontend/src/pages/resources/Qualifications.tsx`
- `frontend/src/pages/resources/Personnel.tsx`
- `frontend/src/pages/resources/HistoryBids.tsx`
- `frontend/src/pages/settings/Settings.tsx`

### Styles
- `frontend/src/styles/global.css` — CSS reset, Chinese-friendly font stack

## Verification
- `npm install` — 227 packages added (0 errors)
- `npx tsc --noEmit` — passed with no errors

## Next Steps
- Task 15: Login page implementation
- Task 16: Workbench page
- Subsequent tasks: Project pages, Resource pages, Settings page
