# 评标办法驱动目录 + 生成后自我评分 设计文档

> 日期：2026-08-28 · 状态：已评审（5 节设计全过）· 前端/后端/部署一体化

## 1. 背景与问题

- 标书的排版和结构必须严格遵守招标文件要求；招标文件含**评标办法**时，生成目录必须参考评标办法、包含其中要求的所有项。
- 标书生成完毕后，需要**自我评分**：按招标文件的评分办法给生成的标书打分。

现状缺口（代码勘察结论）：

- 当前只有 `parsed_requirements_json["evaluation_criteria"]` 一个自由文本字段，无结构化的评标办法/评分标准数据。
- `parse_bid_requirements` 只解析招标文件前 15,000 字符（ai_pipeline.py:131/328），**评标办法章节通常在文中部，经常被截断丢失**。
- `chapter_extractor` 只定位「投标文件格式」章节（pdf_extractor.py:25 `locate_format_pages`），没有任何评标办法定位器。
- `format_verifier.validate_chapter_structure` 对 `evaluation_criteria` 做 naive 拆分 + 标题子串比对（format_verifier.py:519-534），是唯一的“评分项→目录”交叉检查。
- 没有任何自我评分机制。最接近的架构范例是 `verify_format`（纯函数 → 项目 JSON 列 + SSE 事件 + 前端卡片）和 `bid_learning._compare_bid`（AI 逐要求判定 good/partial/missing）。

## 2. 目标与非目标

**目标**

- G1 可靠抽取招标文件的评标办法/评分办法，结构化为「评分指标」（可核对、可修改、可手动补入）。
- G2 目录（章节结构）生成与确认时参考评分指标：内容型缺失指标自动补章并标记「来自评标办法」；质量型指标作为生成指导。
- G3 生成完成后自动自我评分（SSE + 落库），产出总分 + 逐项得分 + 给分依据（引用章节）+ 失分点与改进建议；支持随时重评。
- G4 无法提取评分办法时优雅降级，同时允许手动粘贴评分表补入。

**非目标**

- 不做评分工作台/指标文档库/证据链接体系/历史评分库（YAGNI，方案三否决）。
- 不改财政评审/最低价法等无评分表的评标方式（降级处理即可）。
- 不把自我评分闭环到自动重生成（方案二「评分+回补生成」未选；预留扩展点，不做）。

## 3. 已确认决策（用户评审）

| # | 决策 | 结论 |
|---|------|------|
| Q1 | 目录缺失评分项时 | **自动补齐 + 标记**「来自评标办法」，确认页可改可删 |
| Q2 | 自我评分产出 | **完整报告 + 依据**：总分 + 逐项得分 + 给分依据（引用章节）+ 失分点/补法 |
| Q3 | 评分触发 | **生成完成自动跑 + 可随时重评** |
| Q4 | 提取不到评分办法 | **优雅降级 + 可手动粘贴/编辑补入** |
| Q5 | 总体方案 | **方案一：评分指标作为结构化数据**（一个数据源喂目录补全 + 自我评分） |

## 4. 数据模型

### 4.1 评分指标 `bid_projects.scoring_rubric_json`（唯一真相源）

```json
{
  "status": "found | none | manual",
  "method_name": "综合评分法",
  "max_total": 100,
  "applied": false,
  "raw_text": "提取/粘贴的原始评标办法文本（留痕，可空）",
  "items": [
    {
      "id": "tech-01",
      "dimension": "技术部分",
      "name": "服务方案的完整性与针对性",
      "points": 15,
      "kind": "quality | content | cert | personnel | performance | price",
      "criteria": "方案完整、针对本项目…",
      "key_terms": ["服务方案", "针对性"]
    }
  ]
}
```

- `kind` 语义（目录补全决策依据）：
  - `content`（方案/承诺/实施）、`cert`（资质证书）、`personnel`（人员）、`performance`（业绩）→ **内容型**：需在目录中有对应章节/小节，缺失则自动补。
  - `quality`（完整性/针对性等纯评分维度）、`price`（报价）→ **质量型/报价**：不补节点；quality 仅做生成指导，price 参与评分但内容缺失时该项不计分。
- `max_total` 用于评分折算；加总校验：`sum(item.points) ≈ max_total`，偏差 > 5 分时前端提示需人工核对（非阻塞）。

### 4.2 评分报告 `bid_projects.scoring_report_json`（每次评分覆盖写）

```json
{
  "generated_at": "ISO8601",
  "method_name": "综合评分法",
  "max_total": 100,
  "total": 72,
  "scored_total": 85,
  "unscored_note": "报价项未含报价，未参与计分",
  "items": [
    {
      "id": "tech-01",
      "dimension": "技术部分",
      "name": "服务方案的完整性与针对性",
      "points_total": 15,
      "points_obtained": 11,
      "status": "pass | partial | fail | unscored",
      "evidence": "5.2 服务方案（P18）：…",
      "gap": "缺少对 XX 的针对性说明",
      "suggestion": "补充…"
    }
  ]
}
```

- `total` = 参与计分的所得分；`scored_total` = 参与计分项的满分和；`max_total` = 指标满分（含未计分项，参考用）。
- **最终分数 = `total / scored_total`（按可评分项折算）** —— 有 unscored 项时按其满分剔除折算，防止「没报价/没评上」拉低其他项得分；无 unscored 时 `scored_total == max_total`。报告同时展示 `total/max_total` 供参考。
- 判定：`points_obtained ≥ 90% × points_total` → `pass`；`≥ 60%` → `partial`；否则 `fail`；无内容/判卷失败 → `unscored`。

### 4.3 Schema 变更（alembic 0007）

- `bid_projects` 新增两列：`scoring_rubric_json`（Text, default `"{}"`）、`scoring_report_json`（Text, default `"{}"`）。
- 注意：**已有表加列**，`create_all` 不会建 → 部署需跑 `alembic upgrade head`（区别于既往“新表 stamp”流程）。

## 5. 评分办法提取管线

挂在 `upload_and_parse`（bid.py），与 format 提取并列；失败不阻塞生成。

1. **定位章节** `pdf_extractor.locate_evaluation_section`（新）：克隆 `locate_format_pages`（pdf_extractor.py:25）模式，关键词「评标办法 / 评分办法 / 综合评分法 / 评分标准 / 评审办法 / 评审标准」定位章节页码范围，直接读 PDF 页文本 —— **绕开 15k 截断**。返回 `(start_page, end_page, text)`；定位失败返回 `None`。
2. **AI 结构化提取** `scoring_rubric.extract_rubric(text, ai_adapter)`（新服务 `backend/app/services/scoring_rubric.py`）：喂章节全文，产出 §4.1 结构化指标。输出做 pydantic schema 校验 + 分值加总校验；失败/畸形 → `status="none"`，前端提供手动粘贴。
3. **三态落库**：
   - `found`：提取成功 → 存 `scoring_rubric_json`，确认页可核对修改。
   - `none`：未定位/提取失败 → 目录补全跳过、自我评分隐藏并提示「未检测到评分办法」，UI 提供粘贴入口。
   - `manual`：用户粘贴文本 → `POST /scoring-rubric/parse` 走同一 AI 提取，或直接手填指标。
4. 关键词清单：`评标办法 / 评分办法 / 综合评分法 / 评分标准 / 评审办法 / 评审标准`（用户已确认，运行时仍可能扩展）。

## 6. Feature A — 目录补全（评标办法驱动目录）

**四个消费点**

1. **生成期感知**：`title_refiner.refine_chapter_titles`（title_refiner.py:95）与章节结构生成的提示，用结构化指标替换自由文本 `evaluation_criteria` —— 生成时即对照评分要求。
2. **缺口检测**（替换 format_verifier.py:519-534 naive 拆分）：新纯函数 `gap_detect(rubric, chapter_titles) -> missing_items`。规则：对每个 `kind ∈ {content, cert, personnel, performance}` 的指标项，用其 `key_terms`（≥2 字符）对全部章节/小节标题做包含匹配；任一词未命中任一标题 → 该指标进 `missing_items`。
3. **自动补 + 标记**（`/outline/confirm` 时，chapters.py `_materialise_chapters` 前）：
   - 对 `missing_items` 每项：优先挂在同名 `dimension` 章节（标题包含维度名的章节）下作为新小节；无匹配章节则新建顶层章节。
   - 补的节点标 `source: "scoring_rubric"`；`children_json`/`chapter_structure_json` 节点同样带该标记。
   - 新叶子走正常 token 预算分配，纳入一键生成。
   - **补前可见**：确认页加载时 GET /chapters 顺带计算 `gap_detect`，以「评标办法覆盖」提示条列出将自动补充的指标项（未落库）；用户确认后随 confirm 一次性补入。confirm 响应带 `added_from_rubric` 列表。
   - **幂等**：补入后 rubric 置 `applied: true`（rubric 内容变动后清空）；再次 confirm 以「已存在节点 key_terms 去重 + applied 标记」防重复补；用户删除已补节点后，除非 rubric 再改，不再次强加。
4. **质量型指标**：不补节点。生成指导注入对应 dimension 章节的 `scoring_context`/生成提示；确认页以「评分要求清单」只读面板展示。

## 7. Feature B — 自我评分（AI 判卷 + 代码汇总）

**新服务 `backend/app/services/score_engine.py`**，接线模式照抄 `verify_format`（ai_pipeline.py:1789-1800）。

1. **触发**：`generate_from_chapter_structure` 收尾处（`format_verification` 之后、`done` 之前）自动跑一次；另 `POST /bid/{id}/score` 手动重评（读已落库内容重跑，覆盖报告）。
2. **内容来源**：`ProjectChapter` 行（`final_content || ai_generated_content`）+ `children_json` 小节标题 —— 与导出同一份数据。
3. **判卷**（每 dimension 一次 AI 调用，串行，medium token 预算）：
   - 输入：该维度指标项（points/criteria/key_terms）+ 对应章节全文；dimension↔章节映射：dimension 名匹配章节标题（精确/包含，首个命中）；无匹配维度 → 用全部已组装内容尽力评分（单次调用）。
   - 输出：每项 `points_obtained` + `evidence`（引用章节号/标题）+ `gap` + `suggestion`。
4. **代码汇总**（纯函数，可测）：合计总分、`scored_total` 折算（剔除 unscored）、逐项 `status`（§4.2 阈值）、`points_obtained > points_total` 钳制 + 警告。
5. **边界**：
   - `price` 项无报价内容 → `unscored` + 注记，不拉低其他项。
   - 单项判卷失败 → 该项 `unscored` + 原因，其余照常。
6. **对外**：SSE `scoring_report` 事件（完整报告 JSON）+ 落库 + `GET /bid/{id}/scoring-report`。

## 8. API 表面

| 方法 | 路径 | 说明 |
|------|------|------|
| （内部） | upload_and_parse | 追加评分办法提取（不阻塞） |
| GET | `/bid/{project_id}/scoring-rubric` | 取指标 + status |
| POST | `/bid/{project_id}/scoring-rubric/parse` | 粘贴文本 → AI 提取指标 |
| PUT | `/bid/{project_id}/scoring-rubric` | 保存核对/编辑后的指标 |
| POST | `/bid/{project_id}/outline/confirm` | 现有端点：响应增加补全说明（`added_from_rubric` 列表） |
| SSE | 生成流 | 新增 `scoring_report` 事件 |
| POST | `/bid/{project_id}/score` | 手动重评 → 返回最新报告 |
| GET | `/bid/{project_id}/scoring-report` | 取最新报告（重进项目展示） |

## 9. 前端

1. **评分办法面板**（OutlineConfirm.tsx）：
   - `found` → 可编辑指标列表（改分值/增删项/保存 PUT）。
   - `none` → 「未检测到评分办法」+ 粘贴评分表文本框 + 「解析」按钮（POST parse）+ 直接手填入口。
   - 自动补的章节/小节显示「来自评标办法」徽标（节点 `source === "scoring_rubric"`），可改可删。
   - 「评分要求清单」只读面板（质量型指标）。
2. **自我评分报告**（ProjectWorkflow.tsx 生成结果卡 + 项目页重进可看）：
   - SSE `scoring_report` → 结果卡：总分/可评总分 + 各维度展开逐项得分 + 依据引用 + 失分点 + 补法。
   - 「重新评分」按钮 → `POST /score` → 更新报告。
   - 项目加载时 `GET /scoring-report` 若有则展示。

## 10. 错误处理 / 降级

- `none` 状态：目录补全跳过、评分隐藏并提示；任何时刻不阻塞生成。
- 指标提取畸形：降级 `none` + 粘贴入口；生成照常。
- 评分单项失败：`unscored` + 原因，报告标注，不整体失败。
- 手动粘贴解析失败：前端报错，保留粘贴文本让用户重试或手填。

## 11. 测试

- 纯函数单测：
  - `scoring_rubric`：提取结果 JSON 归一化/校验、分值加总校验、畸形输入降级。
  - `gap_detect`：key_terms 命中/未命中、kind 过滤（quality/price 不参与）、空指标。
  - 补节点构建器：挂到 dimension 章节 / 新建顶层、`source` 标记、去重。
  - `score_engine`：汇总（total/scored_total/status/折算）、超分钳制、unscored 剔除、报告 schema。
  - 迁移前向兼容：缺列时旧数据读取（default `{}`）。
- E2E（e2e_smoke.py 扩展）：预置评分指标 → confirm 后断言自动补节点；生成结束断言收到 `scoring_report` 事件且报告字段完整（真实 AI）。
- 前端：`npm run build` clean + 人工核验。
- 回归：backend 全量 pytest（158+ 现有 + 新增），已知 Windows 编码失败项照常忽略。

## 12. 部署影响

- alembic **0007**（加两列）→ 部署跑 `alembic upgrade head`（**加列走 upgrade**，与既往“新表 stamp”不同；create_all 只建新表不建已有表的新列）。
- 沿用已验证流程：备份 → tar（**排除 backend/app/config.py**）→ pscp → compose 重建 → 迁移 → 验证（401/200、bundle 串、日志）→ 容器内 E2E 冒烟 ×3。
- 服务器本地配置（`config.py` flash 版 / `docker-compose.yml` / `.env`）继续保留不覆盖。
- 回滚：`.deploy-backup-<ts>/` 现成备份；本次迁移仅有加列（DROP COLUMN 即可），低风险。

## 13. 全局约束（沿用项目惯例）

- 新文件钟文 docstring + 版权头；asyncio_mode STRICT（`@pytest.mark.asyncio`）；纯函数可测。
- 现有 SSE 事件（section_start/done/error/progress/outline_generated/format_verification/done/status/chapter_start/done/error）**不破坏**，只新增 `scoring_report`。
- `children_json` 兼容嵌套树 + 扁平任务列表（顶层带 `path` 键 = 扁平）。
- 字段/端点命名沿用现有风格（ snake_case / camelCase 按各端既有习惯）。
- 评分指标/报告均为项目级 JSON，无独立表（YAGNI）。