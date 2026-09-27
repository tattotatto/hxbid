/**
 * 章节结构页纯函数验证 —— 拖拽重排、徽标决策、资源库候选项映射.
 *
 * 为什么是一支脚本而不是单测：前端没有测试框架（package.json 里无
 * vitest/jest），而这三块恰好是"错了就静默出错"的地方 ——
 *   - moveNode 的下标换算错了 → 章节顺序错乱、或把匹配冻到别的章节上
 *   - nodeBadge 漏了附件类 → 打开抽屉的唯一入口消失，整套附件功能不可达
 *   - buildAttachmentOptions 字段名错了 → 候选项静默变成"（无扫描件）"
 * 它们都是纯函数，用 esbuild 转译后直接在 node 里跑即可。
 *
 * 用法：npm run verify:ops
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// 用 fileURLToPath 而不是 URL.pathname：后者不解码百分号，
// 仓库路径里有中文时拼出来的 esbuild 路径是错的
const scriptDir = dirname(fileURLToPath(import.meta.url))
const root = dirname(scriptDir)
const out = mkdtempSync(join(tmpdir(), 'outline-ops-'))

const esbuild = join(root, 'node_modules', '.bin', process.platform === 'win32' ? 'esbuild.cmd' : 'esbuild')
const build = (entry, outfile, extra = []) =>
  execFileSync(esbuild, [entry, '--format=esm', `--outfile=${outfile}`, '--log-level=error', ...extra], {
    cwd: root,
    stdio: ['ignore', 'ignore', 'inherit'],
    shell: process.platform === 'win32',
  })

build('src/components/OutlineEditor/outlineTreeOps.ts', join(out, 'ops.mjs'))
build('src/api/outline.ts', join(out, 'outline.mjs'), ['--bundle'])

const { moveNode, nodeBadge } = await import(pathToFileURL(join(out, 'ops.mjs')).href)
const { buildAttachmentOptions } = await import(pathToFileURL(join(out, 'outline.mjs')).href)

let pass = 0
let fail = 0
const check = (name, got, want) => {
  const g = JSON.stringify(got)
  const w = JSON.stringify(want)
  if (g === w) {
    pass += 1
  } else {
    fail += 1
    console.error(`  FAIL ${name}\n       got  ${g}\n       want ${w}`)
  }
}

// ── moveNode：拖拽重排的下标换算 ──
const tree = () => [
  { title: 'A', children: [] },
  { title: 'B', children: [{ title: 'B1', children: [] }] },
  { title: 'C', children: [] },
]
const order = (t) => t.map((n) => n.title).join(',')
const kids = (t, i) => (t[i].children ?? []).map((n) => n.title).join(',')

check('根节点 A 移到 C 之后', order(moveNode(tree(), [0], [2], 'after')), 'B,C,A')
check('C 移到 A 之前', order(moveNode(tree(), [2], [0], 'before')), 'C,A,B')
check('C 移到 A 之后', order(moveNode(tree(), [2], [0], 'after')), 'A,C,B')
check('A 挂进 C 成为子节点', kids(moveNode(tree(), [0], [2], 'inside'), 1), 'A')
check('A 挂进 C 后根层不再有 A', order(moveNode(tree(), [0], [2], 'inside')), 'B,C')
check('子节点 B1 提到根层', order(moveNode(tree(), [1, 0], [2], 'after')), 'A,B,C,B1')
check('B1 提走后 B 变空', kids(moveNode(tree(), [1, 0], [2], 'after'), 1), '')
check('拖进自己的子树 → 拒绝（同一引用）', moveNode(tree(), [1], [1, 0], 'after') === undefined, false)
{
  const t = tree()
  check('拖进自己的子树 → 返回原数组', moveNode(t, [1], [1, 0], 'after') === t, true)
  check('拖到自己身上 → 返回原数组', moveNode(t, [1], [1], 'inside') === t, true)
}
check('位移后节点数不变', moveNode(tree(), [0], [2], 'after').length, 3)
check(
  '位移后标题集合不变（不丢不重）',
  [...new Set(order(moveNode(tree(), [0], [2], 'after')).split(','))].sort().join(','),
  'A,B,C',
)

// ── nodeBadge：附件类必须有徽标（抽屉的唯一入口） ──
check('附件类有徽标', nodeBadge({ title: 'x', type: 'attachment' })?.kind, 'attachment')
check('附件类空清单文案', nodeBadge({ title: 'x', type: 'attachment' })?.text, '挂附件')
check(
  '附件类有清单时显示数量',
  nodeBadge({ title: 'x', type: 'attachment', attachments: [{}, {}] })?.text,
  '2 个附件',
)
check('固定格式未匹配 → 红标', nodeBadge({ title: 'x', type: 'fixed_form' })?.tone, 'error')
check(
  '固定格式已匹配 → 绿标',
  nodeBadge({ title: 'x', type: 'fixed_form', match: { status: 'matched' } })?.tone,
  'success',
)
check(
  '表格多候选 → 黄标',
  nodeBadge({ title: 'x', type: 'table', match: { status: 'ambiguous' } })?.tone,
  'warning',
)
check('AI 生成不渲染徽标', nodeBadge({ title: 'x', type: 'ai_generated' }), null)
check('缺 type 按 AI 生成处理', nodeBadge({ title: 'x' }), null)
check('mixed 不渲染徽标', nodeBadge({ title: 'x', type: 'mixed' }), null)
check(
  'chapter_type 字段也认（已确认项目）',
  nodeBadge({ title: 'x', chapter_type: 'attachment' })?.kind,
  'attachment',
)

// ── buildAttachmentOptions：三个资源库接口的字段映射 ──
check(
  '资质 {id,name,attachment_path}',
  buildAttachmentOptions('qualification', [
    { id: 'q1', name: '营业执照', attachment_path: 'ocr/a.png' },
  ]),
  [{ value: 'q1', label: '营业执照', path: 'ocr/a.png' }],
)
check(
  '人员证书展开 certificates[]',
  buildAttachmentOptions('personnel_cert', [
    { id: 'p1', name: '张三', certificates: [{ id: 'c1', cert_name: '电工证', attachment_path: 'ocr/b.png' }] },
  ]),
  [{ value: 'c1', label: '张三 · 电工证', path: 'ocr/b.png' }],
)
check('人员无证书 → 不产出候选项', buildAttachmentOptions('personnel_cert', [{ id: 'p1', name: '李四' }]), [])
check(
  '合同从 image_paths_json 取第一张',
  buildAttachmentOptions('contract', [
    { id: 'k1', project_name: '消防服务合同', image_paths_json: '["ocr/c.png"]' },
  ]),
  [{ value: 'k1', label: '消防服务合同', path: 'ocr/c.png' }],
)
check(
  '合同空数组 → path 为空串（不猜路径）',
  buildAttachmentOptions('contract', [{ id: 'k2', project_name: '无图', image_paths_json: '[]' }]),
  [{ value: 'k2', label: '无图', path: '' }],
)
check(
  '合同坏 JSON → 不抛异常、path 为空串',
  buildAttachmentOptions('contract', [{ id: 'k3', project_name: '坏', image_paths_json: '{不是' }]),
  [{ value: 'k3', label: '坏', path: '' }],
)

rmSync(out, { recursive: true, force: true })

console.log(`verify:ops — ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
