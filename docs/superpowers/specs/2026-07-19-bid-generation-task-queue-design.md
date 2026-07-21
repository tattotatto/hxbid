# 标书生成任务队列改造设计

**日期**: 2026-07-19
**状态**: 已确认
**分支**: master

---

## 问题诊断

### 现象
一键生成2000+页标书时，进度始终显示 0/X，长时间等待后全部显示 `[本节内容未生成]`。

### 根因
`ai_pipeline.py` 的 `generate_bid_with_deep_outline()` 将 `generate_section_tree()` 放到 `ThreadPoolExecutor` 的单独线程中运行，该线程内创建新的 `asyncio` 事件循环。

AI adapter（HTTP client session）可能在跨线程/跨事件循环时出现死锁或 hang 住，导致：
1. 所有叶子节点的 AI 调用全部失败
2. `future.result()` 阻塞等待永不返回
3. 进度回调从未被触发 → 前端始终显示 0
4. 最终超时，组装时所有节点内容为空 → `[本节内容未生成]`

### 次要问题
- 无增量持久化：全部完成后才写库，中途崩溃全丢
- 无重试机制：单个小节失败不重试
- 前端反馈单一：只显示数字进度，看不到大纲结构和各节状态

---

## 设计方案

### 核心思路

**去掉线程池，改为在主事件循环中逐个生成，每完成一个小节立即存库。**

```
上传 → 解析 → 信息搜集 → 
  ├─ Phase 1: AI生成深度大纲 → 拆分为任务列表 → 存库
  ├─ Phase 2: 逐节点生成（主循环串行）→ 每节点完成立即COMMIT → SSE推送
  └─ Phase 3: 组装标书 → 输出
```

### 关键改动

#### 1. 去掉 ThreadPoolExecutor（最关键）

`generate_section_tree()` 不再丢到线程池。直接在 SSE event generator 的 async 上下文中逐个调用 AI。

```python
# 新代码（ai_pipeline.py 或 generation_orchestrator.py）
async for event in generate_outline_phase(...):
    yield event

# Phase 2: 直接在 async generator 中逐个生成
for leaf in leaves:
    if leaf_already_done(leaf, generation_state):
        continue
    content = await generate_section(...)  # 直接 await，不用线程池
    save_to_db(leaf, content)
    commit()
    yield sse_event("section_done", leaf)
```

#### 2. 增量持久化（generation_state_json）

`BidProject` 新增 JSON 字段，跟踪每个叶子节点的生成状态：

```json
{
  "status": "generating",
  "total_leaves": 186,
  "sections": {
    "商务部分 > 投标函": {
      "status": "done",
      "content": "...",
      "char_count": 1523,
      "generated_at": "2026-07-19T10:30:00"
    },
    "技术部分 > 应急预案 > 火灾应急预案": {
      "status": "failed",
      "error": "API timeout",
      "retries": 2,
      "content": null
    }
  }
}
```

状态机：`pending → generating → done | failed`

#### 3. 断点续传

生成前检查 `generation_state_json`：
- `done` → 跳过
- `failed` → 根据 retries 决定是否重试
- `pending` → 生成

触发"继续生成"时，只处理 pending 和 failed（未达上限）的节点。

#### 4. 重试机制

```python
MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]  # 指数退避

async def _generate_with_retry(leaf, ...):
    for attempt in range(MAX_RETRIES + 1):
        try:
            content = await generate_section(...)
            return content
        except Exception as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                raise
```

单个节点失败不阻塞后续。全部跑完后列出失败列表，前端可点"重试失败项"。

#### 5. 历史合同/业绩自动匹配

在信息搜集阶段，AI 解析招标文件时检查 `special_requirements` 或 `required_documents` 中是否有业绩/合同要求：
- **有明确要求** → 在对应章节的 context 中注入匹配的历史合同信息
- **无明确要求** → 历史合同信息注入到"投标人认为需要提供的其他内容"章节的 context 中

实现方式：在 `_get_section_guidance()` 中为"其他内容"章节添加历史业绩的引导，在 `assemble_chapter_context()` 中检索历史合同时按需求匹配。

---

## 文件改动清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/app/models/project.py` | 添加字段 | `generation_state_json` (Text/JSON) |
| `backend/app/config.py` | 添加配置 | `GENERATION_MAX_RETRIES`, `GENERATION_RETRY_DELAY`, `GENERATION_SECTION_TIMEOUT` |
| `backend/app/services/ai_pipeline.py` | 重写 | `generate_bid_with_deep_outline()` 移除线程池，改为直接 async |
| `backend/app/services/subsection_generator.py` | 微调 | `generate_section_tree()` 可保留做单节调用，去递归 |
| `backend/app/services/outline_engine.py` | 微调 | 大纲扁平化加入任务初始化逻辑 |
| `backend/app/api/bid.py` | 改动 | `/generate` 端点适配新流程，新增 `/generate/retry-failed` |
| `backend/app/services/rag.py` | 改动 | 历史合同检索增加按需求匹配逻辑 |
| `frontend/src/components/GenerationProgress.tsx` | 重写 | 树形大纲+逐节点进度展示 |
| `frontend/src/pages/project/ProjectWorkflow.tsx` | 改动 | 集成新的生成进度组件 |

---

## 新增 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/bid/generate` | POST | 一键生成（SSE），原有端点，改内部实现 |
| `/api/v1/bid/generate/retry-failed/{project_id}` | POST | 只重试失败的叶子节点 |

---

## 新增配置项

```python
# 标书生成配置
GENERATION_MAX_RETRIES: int = 2
GENERATION_RETRY_DELAY_BASE: float = 1.0
GENERATION_SECTION_TIMEOUT: int = 180  # 单个小节最大秒数
```

---

## SSE 事件类型（新增/变更）

| 事件 | 说明 |
|------|------|
| `outline_ready` | 大纲生成完成，包含 total_leaves, estimated_pages, outline_tree |
| `section_start` | 开始生成某个小节：title, path, index, total |
| `section_chunk` | 小节的流式内容块：path, text |
| `section_done` | 小节生成完成：path, content_length, char_count |
| `section_error` | 小节生成失败：path, error, retry_count |
| `progress` | 总体进度：completed, total, percentage |
| `done` | 全部完成：chapters 数据 |

---

## 自检

1. **占位符检查**：无 TBD/TODO
2. **内部一致性**：大纲→任务→生成→组装 四阶段清晰，无矛盾
3. **范围检查**：聚焦于生成流程改造，不涉及渲染引擎、编辑器等
4. **歧义检查**：历史合同的"明确要求"判定标准 — 通过 AI 解析时提取的 `required_documents` 或 `special_requirements` 中是否包含"业绩""合同""项目经验"等关键词
