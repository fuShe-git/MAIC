import { en } from './en'
import { zh } from './zh'

/** 界面支持的语种 */
export type Lang = 'zh' | 'en'

export type { MessageKey } from './zh'
export { en, zh }

/** 首页示例提示词（按语种维护，避免词条里塞数组） */
export const EXAMPLES: Record<Lang, string[]> = {
  zh: ['从零学 Python，30 分钟写出第一个程序', '用正弦函数讲讲傅里叶变换', '阿瓦隆桌游怎么玩'],
  en: [
    'Learn Python from scratch and ship your first program in 30 minutes',
    'Explain Fourier transform with sine functions',
    'How to play the board game Avalon',
  ],
}

export const MESSAGES: Record<Lang, Record<import('./zh').MessageKey, string>> = { zh, en }
