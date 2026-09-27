/**
 * 真实浏览器冒烟 —— 用本机 Chrome（CDP）打开页面，报出白屏/渲染期异常.
 *
 * **为什么需要它**：2026-09-27 我两次把前端改白屏（`useState<any>(null)` 的状态在
 * 守卫之前被访问），而 `tsc --noEmit` + `npm run build` 全绿 —— 前端没有测试框架，
 * 渲染期崩溃只有真打开页面才知道。这个脚本把那一步自动化了。
 *
 * 零新依赖：Node 18+ 自带 fetch，Node 22+ 自带全局 WebSocket；CDP 直接用它们连。
 *
 * 用法：
 *   node scripts/smoke-page.mjs <baseUrl> <path> [--token <jwt>] [--shot out.png]
 *   node scripts/smoke-page.mjs http://192.168.50.78:8888 / --shot login.png
 *   node scripts/smoke-page.mjs http://192.168.50.78:8888 /projects/<id> --token <jwt>
 *
 * 退出码：0 = 渲染正常；1 = 白屏/有渲染期异常；2 = 环境问题（起不了浏览器等）
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
]

const args = process.argv.slice(2)
// 注意：不能简单 filter(!startsWith('--')) —— 那样 flag 的**值**会被当成位置参数
const FLAGS_WITH_VALUE = new Set(['token', 'shot', 'scroll-to'])
const positional = []
const flags = {}
for (let i = 0; i < args.length; i++) {
  const a = args[i]
  if (a.startsWith('--')) {
    const name = a.slice(2)
    if (FLAGS_WITH_VALUE.has(name)) { flags[name] = args[++i] }
    else { flags[name] = true }
  } else {
    positional.push(a)
  }
}
const flag = (name) => flags[name] ?? null

const baseUrl = (positional[0] || 'http://localhost:8888').replace(/\/$/, '')
const path = positional[1] || '/'
const token = flag('token')
const shot = flag('shot')
const scrollTo = flag('scroll-to')
const PORT = 9222 + (process.pid % 500)

const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p))
if (!chromePath) {
  console.error('找不到 Chrome/Edge，跳过浏览器冒烟')
  process.exit(2)
}

const userDataDir = mkdtempSync(join(tmpdir(), 'smoke-chrome-'))
const chrome = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${userDataDir}`,
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForDevtools() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`)
      if (res.ok) return
    } catch { /* 还没起来 */ }
    await sleep(250)
  }
  throw new Error('DevTools 端口没起来')
}

/** 极简 CDP 客户端：够用即可，不引第三方库 */
function connect(wsUrl) {
  const ws = new WebSocket(wsUrl)
  let nextId = 1
  const pending = new Map()
  const listeners = []
  const ready = new Promise((resolve, reject) => {
    ws.addEventListener('open', () => resolve())
    ws.addEventListener('error', (e) => reject(e))
  })
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    } else if (msg.method) {
      listeners.forEach((fn) => fn(msg))
    }
  })
  return {
    ready,
    onEvent: (fn) => listeners.push(fn),
    send(method, params = {}) {
      const id = nextId++
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        ws.send(JSON.stringify({ id, method, params }))
      })
    },
    close: () => ws.close(),
  }
}

async function main() {
  await waitForDevtools()
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
  const page = targets.find((t) => t.type === 'page')
  if (!page) throw new Error('没有可用的 page target')

  const cdp = connect(page.webSocketDebuggerUrl)
  await cdp.ready

  const errors = []
  cdp.onEvent((msg) => {
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails
      errors.push(`未捕获异常：${d.exception?.description || d.text}`)
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      errors.push(`console.error：${msg.params.args.map((a) => a.value ?? a.description).join(' ')}`)
    }
  })

  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')

  /** 轮询等页面真的渲染出来（bundle 1.8MB，冷启动可能十几秒，别死等固定秒数） */
  async function waitForRoot(timeoutMs) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      await sleep(500)
      try {
        const r = await cdp.send('Runtime.evaluate', {
          expression: `(document.getElementById('root')?.children.length ?? 0) > 0
            && (document.body.innerText || '').trim().length > 5`,
          returnByValue: true,
        })
        if (r.result.value === true) return true
      } catch { /* 导航中执行上下文会丢，继续等 */ }
    }
    return false
  }

  // 需要 token 时先到同源页面写 localStorage（跨源写不进去）
  if (token) {
    await cdp.send('Page.navigate', { url: baseUrl + '/' })
    await sleep(2500)
    await cdp.send('Runtime.evaluate', {
      expression: `localStorage.setItem('token', ${JSON.stringify(token)})`,
    })
    // 关键：中转一下。否则「导航到当前所在 URL」在 CDP 里是 no-op，不会真的重新加载
    await cdp.send('Page.navigate', { url: 'about:blank' })
    await sleep(300)
  }

  await cdp.send('Page.navigate', { url: baseUrl + path })
  const ready = await waitForRoot(25000)
  await sleep(500) // 再给数据请求一点时间

  const probe = await cdp.send('Runtime.evaluate', {
    expression: `JSON.stringify({
      url: location.pathname,
      rootChildren: document.getElementById('root')?.children.length ?? -1,
      textLen: (document.body.innerText || '').trim().length,
      head: (document.body.innerText || '').trim().slice(0, 300),
      readyState: document.readyState,
      scripts: [...document.scripts].map((x) => x.src || '(inline)').slice(0, 5),
      rootExists: !!document.getElementById('root'),
    })`,
    returnByValue: true,
  })
  const info = JSON.parse(probe.result.value)

  if (scrollTo) {
    // 把含指定文字的元素滚进视野 —— 页面很长，默认只截到顶部
    await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        const el = [...document.querySelectorAll('*')].find(
          (e) => e.children.length === 0 && (e.textContent || '').includes(${JSON.stringify(scrollTo)}))
        if (!el) return false
        el.scrollIntoView({ block: 'start' })
        return true
      })()`,
      returnByValue: true,
    })
    await sleep(800)
  }

  if (shot) {
    const img = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(shot, Buffer.from(img.data, 'base64'))
  }

  // 「空白」判据：React 根没有子节点，或整页可见文本几乎为空
  const blank = info.rootChildren <= 0 || info.textLen < 5

  console.log(`  导航到   ${info.url}`)
  console.log(`  25s 内渲染出来  ${ready ? '是' : '否'}`)
  console.log(`  #root 子节点  ${info.rootChildren}`)
  console.log(`  可见文本长度  ${info.textLen}`)
  if (info.head) console.log(`  首屏文本      ${info.head.replace(/\n/g, ' / ').slice(0, 160)}`)
  if (errors.length) {
    console.log(`  ✗ 渲染期异常 ${errors.length} 条：`)
    errors.slice(0, 5).forEach((e) => console.log(`      ${e.slice(0, 300)}`))
  }
  if (shot) console.log(`  截图          ${shot}`)

  if (blank || errors.length) {
    console.log(blank ? '\n结果：白屏 ✗' : '\n结果：有渲染期异常 ✗')
    return 1
  }
  console.log('\n结果：渲染正常 ✓')
  return 0
}

/** 关浏览器 + 清理临时 profile。Chrome 退出前还占着文件，删不掉不是错误 */
async function teardown() {
  try { chrome.kill() } catch { /* 已退出 */ }
  await sleep(600)
  try { rmSync(userDataDir, { recursive: true, force: true }) } catch { /* 占着就留着 */ }
}

main()
  .then(async (code) => { await teardown(); process.exit(code) })
  .catch(async (err) => {
    console.error('冒烟失败：', err.message)
    await teardown()
    process.exit(2)
  })
