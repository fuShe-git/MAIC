import { useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { useI18n } from '../i18n/context'
import type { Lang, MessageKey } from '../i18n/messages'
import {
  deleteGeneratedFile,
  exportGeneratedPptx,
  generatedCoverUrl,
  renameGeneratedFile,
} from '../api/generated'
import type { GeneratedCourse } from '../api/generated'
import {
  assignCourseToFolder,
  createFolder,
  deleteFolder,
  ensureFoldersLoaded,
  listFolders,
  renameFolder,
} from '../api/folders'
import type { CourseFolder } from '../api/folders'
import ConfirmDialog from './ConfirmDialog'
import NewFolderModal from './NewFolderModal'
import './HistorySection.css'

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

/** 备注名长度上限：与后端 /generated/files 的 PATCH 校验保持一致 */
const REMARK_MAX = 60
/** 文件夹名长度上限：与后端 /folders 接口的校验保持一致 */
const FOLDER_NAME_MAX = 40

/** 与今天相差的天数（按自然日计算，忽略时分） */
function dayOffset(iso: string): number {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return Number.NaN
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const targetStart = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  return Math.round((todayStart - targetStart) / 86_400_000)
}

/** 生成时间展示：今天 / 昨天 / N 天前 / 具体日期 */
function formatWhen(iso: string, lang: Lang, t: Translate): string {
  const diff = dayOffset(iso)
  if (Number.isNaN(diff)) return ''
  if (diff <= 0) return t('history.today')
  if (diff === 1) return t('history.yesterday')
  if (diff < 7) return t('history.daysAgo', { n: diff })
  const date = new Date(iso)
  if (lang === 'zh') return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
  return date.toLocaleDateString('en', { month: 'short', day: 'numeric' })
}

function ClockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.4" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 7.8V12l2.8 1.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="6.2" stroke="currentColor" strokeWidth="1.7" />
      <path d="m15.8 15.8 4 4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3.5 7.2A1.7 1.7 0 0 1 5.2 5.5h3.9l1.7 1.9h8a1.7 1.7 0 0 1 1.7 1.7v7.2a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7V7.2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M9 6.8v10.4l8.2-5.2L9 6.8Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}

/** 移动到文件夹：文件夹 + 右向箭头 */
function MoveIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3.6 8.2A1.8 1.8 0 0 1 5.4 6.4h3.4l1.6 1.8h6.2a1.8 1.8 0 0 1 1.8 1.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M3.6 8.2v8.2a1.8 1.8 0 0 0 1.8 1.8h4.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M13.2 13.6h6.4m0 0-2.5-2.5m2.5 2.5-2.5 2.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="m4 20 .9-4.1L16.3 4.5a1.8 1.8 0 0 1 2.6 0l.6.6a1.8 1.8 0 0 1 0 2.6L8.1 19.1 4 20Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="m14.8 6 3.2 3.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

/** 导出 PPT：幻灯片 + 向下箭头 */
function PptIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect
        x="3.6"
        y="4.6"
        width="16.8"
        height="11.4"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M12 7.8v4.6m0 0-1.8-1.8m1.8 1.8 1.8-1.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M8 19.4h8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4.8 7h14.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M9.6 4.8h4.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path
        d="M7.6 7.2 8.4 19a1 1 0 0 0 1 .9h5.2a1 1 0 0 0 1-.9l.8-11.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M10.6 10.6v5.8m2.8-5.8v5.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="m5.6 12.4 4.2 4.2 8.6-8.6"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 5.6v12.8M5.6 12h12.8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

interface HistorySectionProps {
  /** 生成记录列表（后端已按时间倒序） */
  items: GeneratedCourse[]
  /** 是否已结束首次加载 */
  loaded: boolean
  /** 打开某份课件 */
  onOpen: (course: GeneratedCourse) => void
  /** 删除成功：让首页把这条记录从列表里摘掉 */
  onRemove: (courseId: string) => void
  /** 改名成功：把接口返回的备注名同步回首页列表 */
  onRename: (courseId: string, remark: string) => void
}

/** 首页「我的课堂」：胶囊标题栏 + 文件夹 + 课件封面卡片（支持拖拽归类与卡片操作） */
export default function HistorySection({
  items,
  loaded,
  onOpen,
  onRemove,
  onRename,
}: HistorySectionProps) {
  const { t, lang } = useI18n()
  const [keyword, setKeyword] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  // 文件夹存在后端 SQLite：初次进入时拉取，操作以接口为准（失败回滚重拉）
  const [folders, setFolders] = useState<CourseFolder[]>([])
  const [foldersLoaded, setFoldersLoaded] = useState(false)
  const [folderModalOpen, setFolderModalOpen] = useState(false)
  // 正在查看的文件夹 id：非空时进入文件夹视图，只展示该文件夹内的课程
  const [activeFolderId, setActiveFolderId] = useState('')
  // 拖拽中的课件 id / 悬停中的文件夹 id（用于高亮落点）
  const [draggingId, setDraggingId] = useState('')
  const [dragOverFolderId, setDragOverFolderId] = useState('')
  // 封面异步加载状态：loaded 控制淡入，failed 时退回渐变占位
  const [loadedCovers, setLoadedCovers] = useState<Record<string, true>>({})
  const [failedCovers, setFailedCovers] = useState<Record<string, true>>({})
  // 已结算（加载完成或失败）的封面数量：封面在服务端要现渲染，串行排队避免占满连接
  const [settledCount, setSettledCount] = useState(0)
  // 「移动到文件夹」下拉：打开菜单的课件 id
  const [menuId, setMenuId] = useState('')
  // 行内重命名：正在改名的课件 id 与草稿值
  const [renamingId, setRenamingId] = useState('')
  const [renameValue, setRenameValue] = useState('')
  // 取消改名后，输入框失焦仍可能触发一次提交，用这个标记挡掉
  const skipCommitRef = useRef(false)
  // 删除确认弹窗的目标课件 / 提交中 / 失败信息
  const [deleteTarget, setDeleteTarget] = useState<GeneratedCourse | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  // 从「移动到文件夹 → 新建文件夹」进来时，记住要同时移入的课件 id
  const [pendingMoveId, setPendingMoveId] = useState('')
  // 文件夹卡片：「删除文件夹」下拉 / 行内重命名
  const [folderMenuId, setFolderMenuId] = useState('')
  const [renamingFolderId, setRenamingFolderId] = useState('')
  const [folderRenameValue, setFolderRenameValue] = useState('')
  const folderSkipCommitRef = useRef(false)
  // 连同课程一起删文件夹：确认弹窗 / 提交中 / 失败信息
  const [folderDeleteTarget, setFolderDeleteTarget] = useState<CourseFolder | null>(null)
  const [folderDeleting, setFolderDeleting] = useState(false)
  const [folderDeleteError, setFolderDeleteError] = useState('')
  // 轻提示：改名等接口失败的兜底反馈
  const [toast, setToast] = useState('')
  // 正在导出 PPT 的课件 id：用于禁用按钮、防止重复点击
  const [exportingId, setExportingId] = useState('')
  // 状态更新是异步的，用 ref 挡住同一帧内的重复点击
  const exportingRef = useRef(false)

  const courseById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items])
  const assignedIds = useMemo(
    () => new Set(folders.flatMap((folder) => folder.courseIds)),
    [folders],
  )
  // 未归入任何文件夹的课程：留在网格里，可继续拖拽
  const standalone = useMemo(
    () => items.filter((item) => !assignedIds.has(item.id)),
    [items, assignedIds],
  )

  // 当前打开的文件夹（id 失配时自动回到全部课程视图）
  const activeFolder = useMemo(
    () => folders.find((folder) => folder.id === activeFolderId) ?? null,
    [folders, activeFolderId],
  )

  // 文件夹内课程（按存放顺序，过滤掉已不存在的课件）
  const folderCourses = useMemo(() => {
    if (!activeFolder) return []
    return activeFolder.courseIds
      .map((id) => courseById.get(id))
      .filter((course): course is GeneratedCourse => Boolean(course))
  }, [activeFolder, courseById])

  const trimmedKeyword = keyword.trim().toLowerCase()
  // 搜索态：全部视图在全部课程里找（含已归入文件夹的），文件夹视图只在本文件夹内找；
  // 备注名优先于课件原标题参与匹配
  const searchedCourses = useMemo(() => {
    if (!trimmedKeyword) return null
    const pool = activeFolder ? folderCourses : items
    return pool.filter((item) => (item.remark || item.topic).toLowerCase().includes(trimmedKeyword))
  }, [items, folderCourses, activeFolder, trimmedKeyword])

  // 封面加载顺序：搜索态按结果顺序，否则按当前视图要展示的卡片顺序
  const loadOrder = useMemo(() => {
    const list = searchedCourses ?? (activeFolder ? folderCourses : standalone)
    return new Map(list.map((course, index) => [course.id, index]))
  }, [searchedCourses, activeFolder, folderCourses, standalone])

  /** 重新拉取文件夹：接口失败后用它回滚到服务端真实状态 */
  async function refreshFolders(): Promise<void> {
    try {
      const data = await listFolders()
      setFolders(Array.isArray(data.items) ? data.items : [])
    } catch {
      // 服务端不可用时保留当前内存状态，不打扰用户
    }
  }

  // 首次进入：加载文件夹（并发去重 + 旧数据一次性迁移，细节见 api/folders.ts）
  useEffect(() => {
    let alive = true
    ensureFoldersLoaded()
      .then((items) => {
        if (alive) setFolders(items)
      })
      .catch(() => {
        if (alive) setFolders([])
      })
      .finally(() => {
        if (alive) setFoldersLoaded(true)
      })
    return () => {
      alive = false
    }
  }, [])

  // 点击卡片外的任意位置 / 按 Esc 收起卡片上的下拉菜单（移动到文件夹 / 删除文件夹）
  useEffect(() => {
    if (!menuId && !folderMenuId) return
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('.history-menu, .history-card-actions')) return
      setMenuId('')
      setFolderMenuId('')
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuId('')
      setFolderMenuId('')
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menuId, folderMenuId])

  // 轻提示自动消失，避免一直挂在页面上
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  /** 封面加载结束：成功记淡入、失败记占位，并放行下一张 */
  const settleCover = (id: string, ok: boolean) => {
    if (ok) setLoadedCovers((prev) => ({ ...prev, [id]: true }))
    else setFailedCovers((prev) => ({ ...prev, [id]: true }))
    setSettledCount((prev) => prev + 1)
  }

  /** 课件展示名：备注名优先，否则用课件标题 */
  function displayName(course: GeneratedCourse): string {
    return course.remark || course.topic
  }

  /** 导出课件为图片版 PPTX：成功后用临时 object URL 触发浏览器下载 */
  async function handleExportPpt(course: GeneratedCourse) {
    if (exportingRef.current) return
    exportingRef.current = true
    setExportingId(course.id)
    try {
      const blob = await exportGeneratedPptx(course.fileName)
      // 文件名用展示名并去掉文件系统非法字符，空名时回退课程 id
      const baseName =
        displayName(course)
          .replace(/[\\/:*?"<>|]/g, ' ')
          .replace(/\s+/g, ' ')
          .trim() || course.id
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${baseName}.pptx`
      document.body.appendChild(link)
      link.click()
      link.remove()
      // 立即 revoke 可能打断部分浏览器的下载，延迟释放
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      setToast(t('history.exportPptFailed'))
    } finally {
      exportingRef.current = false
      setExportingId('')
    }
  }

  /** 课件所在文件夹 id；空串表示未分组 */
  function folderIdOf(courseId: string): string {
    return folders.find((folder) => folder.courseIds.includes(courseId))?.id ?? ''
  }

  /** 只改本地状态：把课程从所有文件夹里摘掉（课件被删除时用，服务端会自己清理） */
  function dropCourseFromFoldersLocally(courseId: string) {
    setFolders((prev) => {
      if (!prev.some((folder) => folder.courseIds.includes(courseId))) return prev
      return prev.map((folder) => ({
        ...folder,
        courseIds: folder.courseIds.filter((id) => id !== courseId),
      }))
    })
  }

  /** 把课程移入指定文件夹（会先从其它文件夹里摘掉） */
  function moveToFolder(folderId: string, courseId: string) {
    // 先乐观更新，接口失败再回滚
    setFolders((prev) =>
      prev.map((folder) => {
        const rest = folder.courseIds.filter((id) => id !== courseId)
        return folder.id === folderId
          ? { ...folder, courseIds: [...rest, courseId] }
          : { ...folder, courseIds: rest }
      }),
    )
    assignCourseToFolder(courseId, folderId).catch(() => {
      setToast(t('folder.saveFailed'))
      refreshFolders()
    })
  }

  /** 把课程移出所有文件夹（拖回网格空白处 / 选择「未分组」时触发） */
  function removeFromFolders(courseId: string) {
    // 本来就没归组：不用调接口
    if (!assignedIds.has(courseId)) return
    dropCourseFromFoldersLocally(courseId)
    assignCourseToFolder(courseId, '').catch(() => {
      setToast(t('folder.saveFailed'))
      refreshFolders()
    })
  }

  /** 新建文件夹：从卡片菜单进来时，服务端会顺手把该课程移进去 */
  async function handleCreateFolder(name: string) {
    const moveId = pendingMoveId
    setPendingMoveId('')
    setFolderModalOpen(false)
    try {
      const folder = await createFolder(name, moveId || undefined)
      setFolders((prev) => {
        const base = moveId
          ? prev.map((item) => ({
              ...item,
              courseIds: item.courseIds.filter((id) => id !== moveId),
            }))
          : prev
        return [folder, ...base]
      })
    } catch (err) {
      setToast(err instanceof Error ? err.message : t('folder.saveFailed'))
      refreshFolders()
    }
  }

  /** 选中「未分组」或某个文件夹：立即完成归类并收起菜单 */
  function handlePickFolder(folderId: string, course: GeneratedCourse) {
    if (folderId) moveToFolder(folderId, course.id)
    else removeFromFolders(course.id)
    setMenuId('')
  }

  function startRename(course: GeneratedCourse) {
    skipCommitRef.current = false
    setMenuId('')
    setRenameValue(displayName(course))
    setRenamingId(course.id)
  }

  /** 提交改名：空值或与原标题相同 = 清除备注，回到课件自带标题 */
  function commitRename() {
    if (skipCommitRef.current) {
      skipCommitRef.current = false
      return
    }
    const id = renamingId
    if (!id) return
    const course = courseById.get(id)
    const value = renameValue.trim().slice(0, REMARK_MAX)
    setRenamingId('')
    setRenameValue('')
    if (!course) return

    const previous = course.remark ?? ''
    const remark = !value || value === course.topic ? '' : value
    if (remark === previous) return

    // 先本地乐观更新（卡片立即改字），接口失败再回滚并提示
    onRename(id, remark)
    renameGeneratedFile(course.fileName, remark).catch((err: unknown) => {
      onRename(id, previous)
      setToast(err instanceof Error ? err.message : t('history.renameFailed'))
    })
  }

  function cancelRename() {
    // Esc 取消：挡住随后可能的失焦提交
    skipCommitRef.current = true
    setRenamingId('')
    setRenameValue('')
  }

  function openDelete(course: GeneratedCourse) {
    setMenuId('')
    setDeleteError('')
    setDeleteTarget(course)
  }

  /** 确认删除：先删后端文件，成功后再清理本地分组 / 备注 / 列表项 */
  async function handleConfirmDelete() {
    const course = deleteTarget
    if (!course || deleting) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteGeneratedFile(course.fileName)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : t('history.deleteFailed'))
      setDeleting(false)
      return
    }

    // 服务端删课件时会同步清掉文件夹归属，这里只更新本地状态
    dropCourseFromFoldersLocally(course.id)
    setDeleting(false)
    setDeleteTarget(null)
    onRemove(course.id)
  }

  function startFolderRename(folder: CourseFolder) {
    folderSkipCommitRef.current = false
    setMenuId('')
    setFolderMenuId('')
    setFolderRenameValue(folder.name)
    setRenamingFolderId(folder.id)
  }

  /** 提交文件夹改名：空名字视为取消，保留原名 */
  function commitFolderRename() {
    if (folderSkipCommitRef.current) {
      folderSkipCommitRef.current = false
      return
    }
    const id = renamingFolderId
    if (!id) return
    const value = folderRenameValue.trim().slice(0, FOLDER_NAME_MAX)
    setRenamingFolderId('')
    setFolderRenameValue('')
    if (!value) return
    // 先乐观改本地，接口失败再回滚
    setFolders((prev) => {
      if (!prev.some((folder) => folder.id === id && folder.name !== value)) return prev
      return prev.map((folder) => (folder.id === id ? { ...folder, name: value } : folder))
    })
    renameFolder(id, value).catch(() => {
      setToast(t('folder.saveFailed'))
      refreshFolders()
    })
  }

  function cancelFolderRename() {
    // Esc 取消：挡住随后可能的失焦提交
    folderSkipCommitRef.current = true
    setRenamingFolderId('')
    setFolderRenameValue('')
  }

  /** 只删文件夹本身：里面的课程回到「未分组」，课件文件保留 */
  function handleDeleteFolderOnly(folder: CourseFolder) {
    setFolderMenuId('')
    setFolders((prev) => prev.filter((item) => item.id !== folder.id))
    if (activeFolderId === folder.id) backToAll()
    deleteFolder(folder.id).catch(() => {
      setToast(t('folder.saveFailed'))
      refreshFolders()
    })
  }

  function openDeleteFolderWithCourses(folder: CourseFolder) {
    setFolderMenuId('')
    setFolderDeleteError('')
    setFolderDeleteTarget(folder)
  }

  /** 连同课程一起删：逐个删课件文件，失败的不影响其它文件与文件夹删除 */
  async function handleConfirmDeleteFolder() {
    const folder = folderDeleteTarget
    if (!folder || folderDeleting) return
    const targets = folder.courseIds
      .map((id) => courseById.get(id))
      .filter((course): course is GeneratedCourse => Boolean(course))

    setFolderDeleting(true)
    setFolderDeleteError('')
    const results = await Promise.allSettled(
      targets.map(async (course) => {
        try {
          await deleteGeneratedFile(course.fileName)
        } catch (err) {
          // 文件已经不在了（例如别处删过）：仍按删除成功算，避免卡片残留
          if (err instanceof Error && err.message.includes('不存在')) return
          throw err
        }
      }),
    )

    const removedIds: string[] = []
    let failedCount = 0
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') removedIds.push(targets[index].id)
      else failedCount += 1
    })

    // 文件夹本身一定删掉；删文件失败的课程会回到「未分组」继续展示
    setFolders((prev) =>
      prev
        .filter((item) => item.id !== folder.id)
        .map((item) => ({
          ...item,
          courseIds: item.courseIds.filter((id) => !removedIds.includes(id)),
        })),
    )
    removedIds.forEach((id) => onRemove(id))

    setFolderDeleting(false)
    setFolderDeleteTarget(null)
    if (activeFolderId === folder.id) backToAll()

    // 服务端删掉文件夹记录（课件删除时归属已由后端清理）
    deleteFolder(folder.id).catch(() => {
      setToast(t('folder.saveFailed'))
      refreshFolders()
    })
    if (failedCount > 0) setToast(t('folder.deleteFailed', { n: failedCount }))
  }

  /** 课程卡片：封面 + 时间 + 标题 + 悬浮操作（移动 / 改名 / 导出 PPT / 删除） */
  function renderCourseCard(course: GeneratedCourse) {
    const failed = Boolean(failedCovers[course.id])
    const loadedCover = Boolean(loadedCovers[course.id])
    const position = loadOrder.get(course.id) ?? 0
    // 串行加载：前一张结算后才发起下一张，避免多个渲染请求同时挂起
    const shouldLoad = !failed && (loadedCover || position <= settledCount)
    const name = displayName(course)
    const menuOpen = menuId === course.id
    const renaming = renamingId === course.id
    const exporting = exportingId === course.id
    const currentFolderId = folderIdOf(course.id)

    return (
      <div
        className={`history-card ${menuOpen ? 'is-menu-open' : ''}`}
        role="button"
        tabIndex={0}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData('text/plain', course.id)
          setDraggingId(course.id)
          setMenuId('')
        }}
        onDragEnd={() => {
          setDraggingId('')
          setDragOverFolderId('')
        }}
        onClick={() => onOpen(course)}
        onKeyDown={(event) => {
          // 只响应卡片本身：内部按钮/输入框的按键不冒泡触发打开
          if (event.target !== event.currentTarget) return
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          onOpen(course)
        }}
        title={`${name} · ${t('history.open')}`}
      >
        <div className={`history-cover ${failed ? 'is-fallback' : ''}`}>
          {shouldLoad ? (
            <img
              className={`history-cover-img ${loadedCover ? 'is-loaded' : ''}`}
              src={generatedCoverUrl(course.fileName)}
              alt=""
              draggable={false}
              onLoad={() => settleCover(course.id, true)}
              onError={() => settleCover(course.id, false)}
            />
          ) : null}
          <span className="history-cover-play" aria-hidden="true">
            <PlayIcon />
          </span>

          {/* 悬浮操作条：点击不冒泡到卡片，避免误打开课件 */}
          <div
            className="history-card-actions"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <button
              className={`history-act-btn ${menuOpen ? 'is-active' : ''}`}
              type="button"
              title={t('history.moveTo')}
              aria-label={t('history.moveTo')}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuId((prev) => (prev === course.id ? '' : course.id))}
            >
              <MoveIcon />
            </button>
            <button
              className="history-act-btn"
              type="button"
              title={t('history.rename')}
              aria-label={t('history.rename')}
              onClick={() => startRename(course)}
            >
              <PencilIcon />
            </button>
            <button
              className="history-act-btn"
              type="button"
              title={exporting ? t('history.exporting') : t('history.exportPpt')}
              aria-label={t('history.exportPpt')}
              aria-busy={exporting}
              disabled={Boolean(exportingId)}
              onClick={() => handleExportPpt(course)}
            >
              <PptIcon />
            </button>
            <button
              className="history-act-btn is-danger"
              type="button"
              title={t('history.delete')}
              aria-label={t('history.delete')}
              onClick={() => openDelete(course)}
            >
              <TrashIcon />
            </button>
          </div>
        </div>

        <div className="history-row">
          <span className="history-date">
            <ClockIcon />
            {formatWhen(course.createdAt, lang, t)}
          </span>
          {renaming ? (
            <input
              className="history-name-input"
              value={renameValue}
              maxLength={REMARK_MAX}
              autoFocus
              aria-label={t('history.rename')}
              title={t('history.renameHint')}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => setRenameValue(event.target.value)}
              onBlur={commitRename}
              onKeyDown={(event) => {
                event.stopPropagation()
                if (event.key === 'Enter') {
                  event.preventDefault()
                  commitRename()
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  cancelRename()
                }
              }}
            />
          ) : (
            <span className="history-name">{name}</span>
          )}
          <span className="history-open" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <path
                d="M7 17 17 7m0 0h-6.4M17 7v6.4"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </div>

        {menuOpen ? (
          <div
            className="history-menu"
            role="menu"
            aria-label={t('history.moveTo')}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <p className="history-menu-title">{t('history.moveTo')}</p>

            <button
              className="history-menu-item"
              type="button"
              role="menuitemradio"
              aria-checked={currentFolderId === ''}
              onClick={() => handlePickFolder('', course)}
            >
              <span className="history-menu-label">{t('history.ungrouped')}</span>
              {currentFolderId === '' ? (
                <span className="history-menu-check" aria-hidden="true">
                  <CheckIcon />
                </span>
              ) : null}
            </button>

            {folders.map((folder) => {
              const active = currentFolderId === folder.id
              return (
                <button
                  key={folder.id}
                  className="history-menu-item"
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => handlePickFolder(folder.id, course)}
                >
                  <span className="history-menu-label">{folder.name}</span>
                  {active ? (
                    <span className="history-menu-check" aria-hidden="true">
                      <CheckIcon />
                    </span>
                  ) : null}
                </button>
              )
            })}

            <span className="history-menu-sep" aria-hidden="true" />
            <button
              className="history-menu-item is-create"
              type="button"
              role="menuitem"
              onClick={() => {
                setPendingMoveId(course.id)
                setMenuId('')
                setFolderModalOpen(true)
              }}
            >
              <span className="history-menu-icon" aria-hidden="true">
                <PlusIcon />
              </span>
              <span className="history-menu-label">{t('history.newFolder')}</span>
            </button>
          </div>
        ) : null}
      </div>
    )
  }

  /** 文件夹卡片：封面取文件夹内第一门课程，空文件夹用图标占位；支持改名与删除 */
  function renderFolderCard(folder: CourseFolder) {
    const courseList = folder.courseIds
      .map((id) => courseById.get(id))
      .filter((course): course is GeneratedCourse => Boolean(course))
    const coverCourse = courseList[0]
    const dragOver = dragOverFolderId === folder.id
    const menuOpen = folderMenuId === folder.id
    const renaming = renamingFolderId === folder.id

    return (
      <div
        className={`history-card history-folder ${dragOver ? 'is-dragover' : ''} ${menuOpen ? 'is-menu-open' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => {
          setMenuId('')
          setFolderMenuId('')
          setActiveFolderId(folder.id)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          setMenuId('')
          setFolderMenuId('')
          setActiveFolderId(folder.id)
        }}
        title={`${folder.name} · ${t('folder.open')}`}
        onDragOver={(event) => {
          if (!draggingId) return
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
        }}
        onDragEnter={() => {
          if (draggingId) setDragOverFolderId(folder.id)
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return
          setDragOverFolderId('')
        }}
        onDrop={(event) => {
          event.preventDefault()
          event.stopPropagation()
          const courseId = event.dataTransfer.getData('text/plain') || draggingId
          if (courseId) moveToFolder(folder.id, courseId)
          setDraggingId('')
          setDragOverFolderId('')
        }}
      >
        <div className="history-cover history-folder-cover">
          {coverCourse ? (
            <img className="history-cover-img is-loaded" src={generatedCoverUrl(coverCourse.fileName)} alt="" draggable={false} />
          ) : (
            <span className="history-folder-icon" aria-hidden="true">
              <FolderIcon />
            </span>
          )}
          {dragOver ? <span className="history-folder-hint">{t('folder.dragHint')}</span> : null}
          <span className="history-folder-badge">{t('folder.count', { n: courseList.length })}</span>

          {/* 悬浮操作：重命名文件夹 / 删除文件夹（下拉二次选择） */}
          <div
            className="history-card-actions"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <button
              className="history-act-btn"
              type="button"
              title={t('folder.rename')}
              aria-label={t('folder.rename')}
              onClick={() => startFolderRename(folder)}
            >
              <PencilIcon />
            </button>
            <button
              className={`history-act-btn is-danger ${menuOpen ? 'is-active' : ''}`}
              type="button"
              title={t('folder.delete')}
              aria-label={t('folder.delete')}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setFolderMenuId((prev) => (prev === folder.id ? '' : folder.id))}
            >
              <TrashIcon />
            </button>
          </div>
        </div>

        <div className="history-row">
          <span className="history-tag">{t('folder.tag')}</span>
          {renaming ? (
            <input
              className="history-name-input"
              value={folderRenameValue}
              maxLength={FOLDER_NAME_MAX}
              autoFocus
              aria-label={t('folder.rename')}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => setFolderRenameValue(event.target.value)}
              onBlur={commitFolderRename}
              onKeyDown={(event) => {
                event.stopPropagation()
                if (event.key === 'Enter') {
                  event.preventDefault()
                  commitFolderRename()
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  cancelFolderRename()
                }
              }}
            />
          ) : (
            <span className="history-name">{folder.name}</span>
          )}
          <span className="history-folder-arrow" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <path
                d="M10 7.5 14.5 12 10 16.5"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </div>

        {menuOpen ? (
          <div
            className="history-menu is-folder"
            role="menu"
            aria-label={t('folder.delete')}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <button
              className="history-menu-item is-folder-option"
              type="button"
              role="menuitem"
              onClick={() => handleDeleteFolderOnly(folder)}
            >
              <span className="history-menu-icon" aria-hidden="true">
                <FolderIcon />
              </span>
              <span className="history-menu-text">
                <span className="history-menu-label">{t('folder.deleteOnly')}</span>
                <span className="history-menu-desc">{t('folder.deleteOnlyDesc')}</span>
              </span>
            </button>

            {/* 空文件夹没有课程可删，只保留「仅删除文件夹」 */}
            {courseList.length > 0 ? (
              <button
                className="history-menu-item is-folder-option is-danger"
                type="button"
                role="menuitem"
                onClick={() => openDeleteFolderWithCourses(folder)}
              >
                <span className="history-menu-icon" aria-hidden="true">
                  <TrashIcon />
                </span>
                <span className="history-menu-label">
                  {t('folder.deleteWithCourses', { n: courseList.length })}
                </span>
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    )
  }

  const nothingAtAll = items.length === 0 && folders.length === 0

  /** 回到全部课程视图（同时清掉文件夹内的搜索词与浮层） */
  function backToAll() {
    setActiveFolderId('')
    setKeyword('')
    setSearchOpen(false)
    setMenuId('')
    setFolderMenuId('')
    setRenamingId('')
    setRenamingFolderId('')
  }

  // 网格空白处接收拖拽 = 把课程移出文件夹
  const gridDropHandlers = {
    onDragOver: (event: DragEvent<HTMLUListElement>) => {
      if (!draggingId) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'move' as const
    },
    onDrop: (event: DragEvent<HTMLUListElement>) => {
      event.preventDefault()
      const courseId = event.dataTransfer.getData('text/plain') || draggingId
      if (courseId) removeFromFolders(courseId)
      setDraggingId('')
      setDragOverFolderId('')
    },
  }

  return (
    <section className="history">
      <div className="history-bar">
        <span className="history-rule" aria-hidden="true" />

        <div className="history-pill">
          <span className="history-pill-title">
            <ClockIcon />
            {activeFolder ? (
              <>
                <button className="history-crumb" type="button" onClick={backToAll}>
                  {t('history.title')}
                </button>
                <span className="history-crumb-sep" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none">
                    <path
                      d="M10 7.5 14.5 12 10 16.5"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <span className="history-crumb is-current">{activeFolder.name}</span>
              </>
            ) : (
              <span>{t('history.title')}</span>
            )}
          </span>
          <span className="history-pill-count">{activeFolder ? folderCourses.length : items.length}</span>
          <span className="history-pill-actions">
            <button
              className={`history-pill-btn ${searchOpen ? 'is-active' : ''}`}
              type="button"
              title={t('history.search')}
              aria-label={t('history.search')}
              aria-pressed={searchOpen}
              onClick={() => {
                setSearchOpen((prev) => {
                  const next = !prev
                  if (!next) setKeyword('')
                  return next
                })
              }}
            >
              <SearchIcon />
            </button>

            {searchOpen ? (
              <span className="history-pill-search">
                <SearchIcon />
                <input
                  className="history-pill-input"
                  value={keyword}
                  autoFocus
                  placeholder={t('history.searchPlaceholder')}
                  onChange={(event) => setKeyword(event.target.value)}
                  onKeyDown={(event) => {
                    // Esc 收起搜索，与常见搜索框行为一致
                    if (event.key !== 'Escape') return
                    setKeyword('')
                    setSearchOpen(false)
                  }}
                />
              </span>
            ) : null}

            <button
              className="history-pill-btn"
              type="button"
              title={t('folder.new')}
              aria-label={t('folder.new')}
              onClick={() => setFolderModalOpen(true)}
            >
              <FolderIcon />
            </button>
          </span>
        </div>

        <span className="history-rule" aria-hidden="true" />
      </div>

      {!loaded || !foldersLoaded ? (
        <div className="history-empty">{t('history.loading')}</div>
      ) : activeFolder ? (
        <>
          {/* 文件夹视图：返回 + 文件夹名 + 课程数量 */}
          <div className="history-folder-head">
            <button
              className="history-folder-back"
              type="button"
              onClick={backToAll}
              title={t('folder.back')}
              aria-label={t('folder.back')}
            >
              <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M14.5 7.5 10 12l4.5 4.5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <h3 className="history-folder-name">{activeFolder.name}</h3>
            <span className="history-folder-count">{t('folder.count', { n: folderCourses.length })}</span>
          </div>

          {searchedCourses ? (
            searchedCourses.length === 0 ? (
              <div className="history-empty">{t('history.noMatch')}</div>
            ) : (
              <ul className="history-list" {...gridDropHandlers}>
                {searchedCourses.map((course) => (
                  <li key={course.id}>{renderCourseCard(course)}</li>
                ))}
              </ul>
            )
          ) : folderCourses.length === 0 ? (
            <div className="history-empty">{t('folder.emptyHint')}</div>
          ) : (
            <ul className="history-list" {...gridDropHandlers}>
              {folderCourses.map((course) => (
                <li key={course.id}>{renderCourseCard(course)}</li>
              ))}
            </ul>
          )}
        </>
      ) : nothingAtAll ? (
        <div className="history-empty">{t('history.empty')}</div>
      ) : searchedCourses ? (
        searchedCourses.length === 0 ? (
          <div className="history-empty">{t('history.noMatch')}</div>
        ) : (
          <ul className="history-list" {...gridDropHandlers}>
            {searchedCourses.map((course) => (
              <li key={course.id}>{renderCourseCard(course)}</li>
            ))}
          </ul>
        )
      ) : (
        <ul className="history-list" {...gridDropHandlers}>
          {folders.map((folder) => (
            <li key={folder.id}>{renderFolderCard(folder)}</li>
          ))}
          {standalone.map((course) => (
            <li key={course.id}>{renderCourseCard(course)}</li>
          ))}
        </ul>
      )}

      <NewFolderModal
        open={folderModalOpen}
        onClose={() => {
          setPendingMoveId('')
          setFolderModalOpen(false)
        }}
        onCreate={handleCreateFolder}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title={t('history.deleteTitle')}
        error={deleteError}
        confirmLabel={t('history.delete')}
        cancelLabel={t('common.cancel')}
        danger
        busy={deleting}
        onConfirm={handleConfirmDelete}
        onCancel={() => {
          if (deleting) return
          setDeleteTarget(null)
          setDeleteError('')
        }}
      />

      <ConfirmDialog
        open={Boolean(folderDeleteTarget)}
        title={t('folder.deleteTitle')}
        description={t('folder.deleteConfirmDesc', {
          n: folderDeleteTarget?.courseIds.length ?? 0,
        })}
        error={folderDeleteError}
        confirmLabel={t('history.delete')}
        cancelLabel={t('common.cancel')}
        danger
        busy={folderDeleting}
        onConfirm={handleConfirmDeleteFolder}
        onCancel={() => {
          if (folderDeleting) return
          setFolderDeleteTarget(null)
          setFolderDeleteError('')
        }}
      />

      {toast ? (
        <div className="history-toast" role="status">
          {toast}
        </div>
      ) : null}
    </section>
  )
}
