/**
 * 第 336 项：视频在详情栏出缩略图。
 *
 * 后端 /api/thumb 早就有视频分支（Windows Shell 缩略图取一帧，8 秒超时 + 磁盘缓存），
 * 前端只是没把视频扩展名放进详情白名单。这里钉死白名单，谁把它删了这条就红。
 * 网格视图（App.tsx 的 THUMB_EXT）故意不加视频 —— 一屏几十格并发走 Shell COM 是 312 项事故来路。
 */
import { describe, expect, it } from 'vitest'
import PANELS from './panels.tsx?raw'
import SRC from '../App.tsx?raw'

function listBlock(src: string, name: string) {
  const start = src.indexOf(`const ${name} = [`)
  expect(start).toBeGreaterThan(-1)
  const end = src.indexOf(']', start)
  expect(end).toBeGreaterThan(start)
  return src.slice(start, end + 1)
}

describe('第 336 项：详情栏视频缩略图', () => {
  it('详情白名单包含常见视频扩展名', () => {
    const block = listBlock(PANELS, 'THUMB_IN_DTL')
    for (const e of ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'm4v', 'webm']) {
      expect(block).toContain(`'${e}'`)
    }
  })

  it('图片 / PDF 的白名单没被顺手删掉', () => {
    const block = listBlock(PANELS, 'THUMB_IN_DTL')
    for (const e of ['png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf']) {
      expect(block).toContain(`'${e}'`)
    }
  })

  it('网格视图仍然不给视频加缩略图（防 312 项那种并发卡死）', () => {
    const block = listBlock(SRC, 'THUMB_EXT')
    expect(block).not.toContain('mp4')
    expect(block).not.toContain('mkv')
  })
})
