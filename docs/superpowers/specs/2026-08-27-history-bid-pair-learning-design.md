# 历史标书改版:招标文件 + 标书配对学习

日期:2026-08-27
状态:已确认(2026-08-27 用户批准,进入实施)

## 背景与目标

资源库「历史标书」当前只支持上传单个标书文件,并把它当作参考材料向量化。
用户要求改写为:**上传时必须同时上传招标文件 + 标书**;上传后**先分析招标文件**,
再**对比标书**产出「标书应该怎么写」的经验。

## 已确认的产品决策(来自澄清问答)

1. **学习产出 = 两者都要**:
   - 结构化「投标写法学习报告」(人可查看):招标文件每条要求/每节结构 → 标书怎么写的、格式是否合规、写作风格。
   - 把「招标要求 → 标书应答」对齐对索引进 RAG,生成时可按相似要求召回「当时是怎么应答的」。
2. **触发时机**:上传成功后自动异步分析(列表显示进度状态)。
3. **老数据**:历史库只用新格式;老的「只有标书」条目数据保留但不再展示。
4. **内容来源**:
   - 新上传的「招标 + 标书」配对。
   - 系统内已完成/已中标项目(其招标文件在系统里、标书为生成结果)以配对形式进入历史库参与学习。

## 数据模型

新增表 `bid_lesson`(不动 `bid_projects` 结构):

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | |
| name | String(300) | 标书名 |
| source_type | String(20) | `upload` / `project` |
| project_id | String(36) FK nullable | 系统项目来源时关联 |
| tender_path | String(500) nullable | 上传对的招标文件路径;系统项目复用 original_file_path |
| bid_path | String(500) nullable | 上传对的标书路径;系统项目由章节内容组装 |
| status | String(20) | pending / analyzing / ready / failed |
| error | Text nullable | 失败原因 |
| lesson_json | Text default "{}" | 结构化学习报告 |
| created_by | String(36) nullable | |
| created_at / updated_at | DateTime | |

## 分析流水线(新服务 `bid_learning.py`,异步)

三段式,复用现有能力(`format_extractor` / `parse_bid_requirements` / `outline_engine`):

1. **分析招标文件**:提取需求清单、目录结构、格式要求。
2. **对比标书**:招标文件每节/每条要求 → 在标书里定位对应应答,产出逐条对照
   (是否应答、怎么应答、质量 完整/部分/缺失、格式是否合规、写作风格特征)。
3. **产出**:
   - `lesson_json` 结构化报告(需求覆盖表 + 结构映射表 + 风格总结 + 若干「标书应该怎么写」要点)。
   - 把「招标要求 → 标书应答」对齐片段按 `source="lesson"` 索引进向量库。

**成本控制**:上传对立即自动分析;系统项目入列后自动分析但排队串行(一次一个)。

## RAG 消费

`rag.retrieve_similar_chapters` 之外新增一路:按「相似招标要求」召回已学习的对齐对,
把「这条要求当时是怎么应答的」注入生成 prompt 的 `reference_sections`。

## API + 前端

- **API**(`/bid-lessons` 路由):
  - `POST /bid-lessons/upload` — 两个必传文件 + 名称,自动启动分析
  - `GET /bid-lessons` — 列表(含系统完成项目,懒创建配对)
  - `GET /bid-lessons/{id}` — 详情 + 报告
  - `POST /bid-lessons/{id}/relearn` — 重试/重新学习
  - `DELETE /bid-lessons/{id}` — 删除
- **前端 `HistoryBids.tsx` 改写**:
  - 列表:名称、来源(上传/系统项目)、状态(分析中/已学习/失败)、操作(查看报告/重试/删除)。
  - 上传弹窗:两个必传文件(招标文件 + 标书)+ 名称,校验齐全才能提交。
  - 学习报告:行内展开或抽屉查看。
  - 状态轮询(异步分析)。

## 测试与验证

- 单元测试:`bid_learning` 分段函数、报告结构校验。
- 端到端:`e2e_smoke` 增加上传一对文件 → 自动分析 → 报告 ready 断言。
- 回归:现有测试不受影响。

## 涉及文件

- 新增:`backend/app/models/bid_lesson.py`、`backend/app/services/bid_learning.py`、alembic 迁移
- 修改:`backend/app/api/bid.py`(或新 `bid_lesson.py` 路由)、`backend/app/services/rag.py`、`backend/app/services/ai_pipeline.py`、`frontend/src/pages/resources/HistoryBids.tsx`、`frontend/src/api/client.ts`、`backend/scripts/e2e_smoke.py`
