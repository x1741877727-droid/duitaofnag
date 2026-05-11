// Wizard 公开 API.
//
// 用法:
//   import { PerfWizard, useWizardAutoOpen } from '@/components/wizard'
//
//   function App() {
//     const { open, close } = useWizardAutoOpen()
//     return (
//       <>
//         <PerfWizard open={open} onClose={close} />
//         {/* ...rest */}
//       </>
//     )
//   }

export { PerfWizard } from './PerfWizard'
export { wizardApi } from './wizardApi'
export type {
  HardwareInfo,
  Plan,
  AuditItem,
  AuditReport,
  AuditResponse,
  RuntimeMode,
  RuntimeProfile,
  WizardStep,
} from './types'

import { useEffect, useState } from 'react'
import { wizardApi } from './wizardApi'
import { useAppStore } from '@/lib/store'

/**
 * 启动时调一次 audit, 如果 needs_action=true 就自动打开 wizard.
 *
 * 强制打开:
 *   - URL 加 ?wizard=1
 *   - 调 reopen() (用户在 Settings 里点"重新优化"按钮)
 *
 * 用户跳过过的话, 第二次进 dashboard 不再弹 (本地 sessionStorage).
 */
export function useWizardAutoOpen() {
  const [open, setOpen] = useState(false)
  const [skipDetect, setSkipDetect] = useState(false)
  const wizardForceOpen = useAppStore((s) => s.wizardForceOpen)
  const setWizardForceOpen = useAppStore((s) => s.setWizardForceOpen)

  // Settings 按钮 / 其他组件触发的强制打开
  useEffect(() => {
    if (wizardForceOpen) {
      setSkipDetect(true)
      setOpen(true)
    }
  }, [wizardForceOpen])

  // 启动 1 次自动 audit (URL ?wizard=1 / sessionStorage / needs_action)
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      if (params.get('wizard') === '1') {
        setOpen(true)
        return
      }
    } catch {
      // ignore
    }

    if (sessionStorage.getItem('wizard.dismissed') === '1') return
    let cancelled = false
    ;(async () => {
      try {
        const audit = await wizardApi.audit(6)
        if (cancelled) return
        if (audit.report.needs_action) setOpen(true)
      } catch {
        // 忽略, 不阻塞 dashboard
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  function close() {
    sessionStorage.setItem('wizard.dismissed', '1')
    setOpen(false)
    setSkipDetect(false)
    setWizardForceOpen(false)
    try {
      const url = new URL(window.location.href)
      if (url.searchParams.has('wizard')) {
        url.searchParams.delete('wizard')
        window.history.replaceState({}, '', url.toString())
      }
    } catch {
      // ignore
    }
  }
  function reopen() {
    sessionStorage.removeItem('wizard.dismissed')
    setSkipDetect(true)
    setOpen(true)
  }

  return { open, close, reopen, skipDetect }
}
