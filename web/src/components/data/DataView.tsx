import { useAppStore, type DataSubView } from '@/lib/store'
import { Archive as ArchiveIcon, Brain, Target } from 'lucide-react'
import { Archive } from '@/components/archive/Archive'
import { MemoryView } from '@/components/memory/MemoryView'
import { OracleView } from '@/components/oracle/OracleView'
import { SubTabBar } from '@/components/recognition/RecognitionView'

const TABS: { key: DataSubView; label: string; Icon: typeof ArchiveIcon; title: string }[] = [
  { key: 'archive', label: '决策档案', Icon: ArchiveIcon, title: '历史会话 / 决策档案' },
  { key: 'memory',  label: '记忆库',   Icon: Brain,       title: 'Memory L1 浏览' },
  { key: 'oracle',  label: 'Oracle',  Icon: Target,      title: '决策回放 / 回归' },
]

export function DataView() {
  const { dataSubView, setDataSubView } = useAppStore()
  return (
    <div className="h-full flex flex-col">
      <SubTabBar
        tabs={TABS}
        active={dataSubView}
        onChange={k => setDataSubView(k as DataSubView)}
      />
      <div className="flex-1 min-h-0 overflow-auto">
        {dataSubView === 'archive' && <Archive />}
        {dataSubView === 'memory'  && <MemoryView />}
        {dataSubView === 'oracle'  && <OracleView />}
      </div>
    </div>
  )
}
