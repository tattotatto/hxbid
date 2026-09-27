/**
 * 章节结构树的纯操作函数.
 *
 * 从 OutlineTree.tsx 抽出：这些是纯函数，不依赖 React/antd，抽出来既能
 * 独立验证（拖拽重排的下标换算是本功能最容易出错的地方），也让组件只管渲染。
 */
import type { OutlineChapter } from '../../api/outline'

export type FlatNode = {
  key: string
  chapter: OutlineChapter
  parent: OutlineChapter[] | null
  path: number[] // index path from root
}

export function flatten(
  chapters: OutlineChapter[],
  parent: OutlineChapter[] | null,
  basePath: number[],
): FlatNode[] {
  const out: FlatNode[] = []
  chapters.forEach((ch, idx) => {
    const path = [...basePath, idx]
    const key = path.join('.')
    out.push({ key, chapter: ch, parent, path })
    if (ch.children && ch.children.length > 0) {
      out.push(...flatten(ch.children, chapters, path))
    }
  })
  return out
}

export function getNode(chapters: OutlineChapter[], path: number[]): OutlineChapter | null {
  let arr: OutlineChapter[] | undefined = chapters
  let node: OutlineChapter | null = null
  for (const idx of path) {
    if (!arr) return null
    node = arr[idx]
    arr = node?.children
  }
  return node
}

export function updateNode(
  chapters: OutlineChapter[],
  path: number[],
  updater: (n: OutlineChapter) => OutlineChapter,
): OutlineChapter[] {
  if (path.length === 0) return chapters
  const [head, ...rest] = path
  return chapters.map((ch, idx) => {
    if (idx !== head) return ch
    if (rest.length === 0) return updater(ch)
    return { ...ch, children: updateNode(ch.children ?? [], rest, updater) }
  })
}

export function removeNode(chapters: OutlineChapter[], path: number[]): OutlineChapter[] {
  if (path.length === 0) return chapters
  const [head, ...rest] = path
  if (rest.length === 0) {
    return chapters.filter((_, idx) => idx !== head)
  }
  return chapters.map((ch, idx) => {
    if (idx !== head) return ch
    return { ...ch, children: removeNode(ch.children ?? [], rest) }
  })
}

export function insertNode(
  chapters: OutlineChapter[],
  parentPath: number[],
  position: 'child' | 'after',
  newNode: OutlineChapter,
): OutlineChapter[] {
  if (position === 'after') {
    // 插入到 parentPath 节点之后（同级）。parentPath = [] 表示插到最前面。
    if (parentPath.length === 0) {
      return [newNode, ...chapters]
    }
    const [head, ...rest] = parentPath
    return chapters.map((ch, idx) => {
      if (idx !== head) return ch
      if (rest.length === 0) {
        // 当前节点是兄弟的目标 → 把 newNode 紧接其后
        // 这里 rest=[] 但 parentPath 非空，意味着在某个父节点里。
        // 父节点的 children 在下一层 updateNode 处理。
        return ch
      }
      return { ...ch, children: insertNode(ch.children ?? [], rest, 'after', newNode) }
    })
  }

  // child: 在 parentPath 节点下添加子节点
  if (parentPath.length === 0) {
    return [...chapters, newNode]
  }
  const [head, ...rest] = parentPath
  return chapters.map((ch, idx) => {
    if (idx !== head) return ch
    const newChildren =
      rest.length === 0
        ? [...(ch.children ?? []), newNode]
        : [...(ch.children ?? [])].map((c, i) =>
            i === rest[0] ? insertNode([c], rest.slice(1), 'child', newNode)[0] : c,
          )
    return { ...ch, children: newChildren }
  })
}

/** candidatePath 是否位于 ancestorPath 的子树内 */
export function isDescendant(ancestorPath: number[], candidatePath: number[]): boolean {
  if (candidatePath.length <= ancestorPath.length) return false
  return ancestorPath.every((v, i) => candidatePath[i] === v)
}

/** 从 removedPath 删掉一个节点后，同一父级下排在它后面的兄弟下标要减一 */
export function shiftPath(path: number[], removedPath: number[]): number[] {
  const parent = removedPath.slice(0, -1)
  const sameParent =
    path.length > parent.length && parent.every((v, i) => path[i] === v)
  if (!sameParent) return path
  const out = [...path]
  if (out[parent.length] > removedPath[removedPath.length - 1]) {
    out[parent.length] -= 1
  }
  return out
}

/** 在 parentPath 的第 index 个位置插入节点 */
export function insertAt(
  chapters: OutlineChapter[],
  parentPath: number[],
  index: number,
  node: OutlineChapter,
): OutlineChapter[] {
  const clamp = (n: number, max: number) => Math.max(0, Math.min(n, max))
  if (parentPath.length === 0) {
    const next = [...chapters]
    next.splice(clamp(index, next.length), 0, node)
    return next
  }
  const [head, ...rest] = parentPath
  return chapters.map((ch, i) => {
    if (i !== head) return ch
    if (rest.length === 0) {
      const kids = [...(ch.children ?? [])]
      kids.splice(clamp(index, kids.length), 0, node)
      return { ...ch, children: kids }
    }
    return { ...ch, children: insertAt(ch.children ?? [], rest, index, node) }
  })
}

/**
 * 把 fromPath 的节点移动到 toPath 的相对位置。
 * position: 'before' | 'after' 挂成兄弟；'inside' 挂成子节点。
 * 拖进自己的子树会被拒绝（否则节点会被复制成自己的后代）。
 */
export function moveNode(
  chapters: OutlineChapter[],
  fromPath: number[],
  toPath: number[],
  position: 'before' | 'after' | 'inside',
): OutlineChapter[] {
  const node = getNode(chapters, fromPath)
  if (!node) return chapters
  if (isDescendant(fromPath, toPath)) return chapters
  if (fromPath.join('.') === toPath.join('.')) return chapters

  const without = removeNode(chapters, fromPath)
  const target = shiftPath(toPath, fromPath)

  if (position === 'inside') {
    return updateNode(without, target, (n) => ({
      ...n,
      children: [...(n.children ?? []), node],
    }))
  }
  const parentPath = target.slice(0, -1)
  const index = target[target.length - 1] + (position === 'after' ? 1 : 0)
  return insertAt(without, parentPath, index, node)
}
