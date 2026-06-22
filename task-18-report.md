# Task 18: 资源库管理 & 设置页面

**Status:** complete
**Date:** 2026-06-22

## Files Changed

### 1. `frontend/src/pages/resources/Qualifications.tsx` — Replaced stub
Full CRUD page for company qualifications:
- Header: "公司资质" title + "添加资质" button (PlusOutlined, primary)
- Table columns: 资质名称, 证书编号, 颁发机构, 到期日期, 操作(编辑/删除)
- Modal form: name (required), cert_number, issuing_authority, issue_date (DatePicker), expiry_date (DatePicker)
- API: GET/POST /qualifications/, PUT/DELETE /qualifications/{id}
- Date conversion: dayjs format 'YYYY-MM-DD'
- Delete with Popconfirm

### 2. `frontend/src/pages/resources/Personnel.tsx` — Replaced stub
Full CRUD page for personnel:
- Table columns: 姓名, 学历, 联系电话, 标签, 操作(编辑/删除)
- Modal form: name (required), education, phone, tags
- Create includes empty experiences:[] and certificates:[]
- API: GET/POST /personnel/, PUT/DELETE /personnel/{id}

### 3. `frontend/src/pages/resources/HistoryBids.tsx` — Replaced stub
Historical bid document list:
- Fetches GET /projects/ and filters status in ['exported', 'archived', 'won', 'lost']
- Table columns: 项目名称, 中标结果 (Tag: won=success "已中标", lost=error "未中标", else "待定"), 创建时间

### 4. `frontend/src/pages/settings/Settings.tsx` — Replaced stub
System settings with two Cards:
- "AI 模型配置": ai_provider (Select), api_key (Input.Password), temperature (0-1, step 0.1)
  - Saved to localStorage('settings')
  - Loaded on mount from localStorage
- "通知配置": wecom_webhook, dingtalk_webhook

## Verification
- `cd frontend && npx tsc --noEmit` passes clean (zero errors)
