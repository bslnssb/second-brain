import type { SVGProps } from 'react'

/**
 * 图标全部用内联 SVG，不用 emoji。
 * 理由：emoji 由系统字体渲染，深色模式下颜色不受控（有时是彩色、有时发灰），
 * 而且不同 Windows 版本字形不一样。这里的图标统一 currentColor、20×20 网格、
 * 描边 1.6，和 13px 的中文正文字重能搭上。
 */
type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Svg({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const IconSearch = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8.6" cy="8.6" r="5.4" />
    <path d="M12.8 12.8 17 17" />
  </Svg>
)

export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="10" cy="10" r="7" />
    <path d="M10 6v4.4l2.8 1.7" />
  </Svg>
)

export const IconGrid = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="6" height="6" rx="1.4" />
    <rect x="11" y="3" width="6" height="6" rx="1.4" />
    <rect x="3" y="11" width="6" height="6" rx="1.4" />
    <rect x="11" y="11" width="6" height="6" rx="1.4" />
  </Svg>
)

export const IconList = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 5h11M6 10h11M6 15h11" />
    <circle cx="3.2" cy="5" r=".9" fill="currentColor" stroke="none" />
    <circle cx="3.2" cy="10" r=".9" fill="currentColor" stroke="none" />
    <circle cx="3.2" cy="15" r=".9" fill="currentColor" stroke="none" />
  </Svg>
)

export const IconGear = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.8 6.2h14.4M2.8 13.8h14.4" />
    <circle cx="7.4" cy="6.2" r="1.9" />
    <circle cx="12.6" cy="13.8" r="1.9" />
  </Svg>
)

export const IconFolder = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 6.4c0-.8.6-1.4 1.4-1.4h3l1.6 1.8h6.6c.8 0 1.4.6 1.4 1.4v6.4c0 .8-.6 1.4-1.4 1.4H4.4c-.8 0-1.4-.6-1.4-1.4z" />
  </Svg>
)

export const IconFile = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.4 3h6l3.2 3.2v10.4c0 .8-.6 1.4-1.4 1.4H5.4c-.8 0-1.4-.6-1.4-1.4V4.4c0-.8.6-1.4 1.4-1.4z" />
    <path d="M11.2 3v3.6h3.4" />
  </Svg>
)

export const IconDoc = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.4 3h6l3.2 3.2v10.4c0 .8-.6 1.4-1.4 1.4H5.4c-.8 0-1.4-.6-1.4-1.4V4.4c0-.8.6-1.4 1.4-1.4z" />
    <path d="M7 9.6h6M7 12.4h4" />
  </Svg>
)

export const IconImage = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4.4" width="14" height="11.2" rx="1.6" />
    <circle cx="7.4" cy="8.4" r="1.2" />
    <path d="M3.4 13.4 8 10l3 2.4 2.6-2 3 2.6" />
  </Svg>
)

export const IconZip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.4 3h9.2c.8 0 1.4.6 1.4 1.4v11.2c0 .8-.6 1.4-1.4 1.4H5.4c-.8 0-1.4-.6-1.4-1.4V4.4c0-.8.6-1.4 1.4-1.4z" />
    <path d="M9 3v3M11 6v3M9 9v2.4h2V9" />
  </Svg>
)

export const IconApp = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3.2" y="3.2" width="13.6" height="13.6" rx="3" />
    <path d="M7.2 12.6 10 7l2.8 5.6" />
  </Svg>
)

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.6 5.6l8.8 8.8M14.4 5.6l-8.8 8.8" />
  </Svg>
)

export const IconHelp = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="10" cy="10" r="7.2" />
    <path d="M8 7.8c0-1.1.9-1.9 2-1.9s2 .8 2 1.9c0 1.4-1.9 1.4-1.9 3" />
    <circle cx="10" cy="13.8" r=".85" fill="currentColor" stroke="none" />
  </Svg>
)

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.4 10.4l3.6 3.6 7.6-8" />
  </Svg>
)

export const IconStop = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5.4" y="5.4" width="9.2" height="9.2" rx="2" />
  </Svg>
)

export const IconRefresh = (p: IconProps) => (
  <Svg {...p}>
    <path d="M16.2 9.2a6.4 6.4 0 1 0-1.3 5.3" />
    <path d="M16.4 4.6v4.8h-4.8" />
  </Svg>
)

// 侧栏用：视频 / 音频（原来只有图片和压缩包）
export const IconVideo = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="5.5" width="12" height="10" rx="2" />
    <path d="M15 9.5l4-2.2v6.4L15 11.5z" />
  </Svg>
)

export const IconAudio = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 14.5V6l6-1.2v8.2" />
    <circle cx="7" cy="15" r="2" />
    <circle cx="15" cy="13.8" r="2" />
  </Svg>
)

/* 顶部「切到简要搜索」用：一个小窗 + 放大镜，和左侧搜索框的放大镜区分开 */
export function IconMini({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2.5" y="4" width="15" height="11.5" rx="2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2.5 7.2h15" stroke="currentColor" strokeWidth="1.1" />
      <circle cx="8.6" cy="11" r="1.9" stroke="currentColor" strokeWidth="1.3" />
      <path d="M10 12.4l1.5 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}
