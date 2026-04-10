

import { useAppStore } from '@/lib/store'
import { DeployView } from './deploy-view'
import { BattleView } from './battle-view'

export function Dashboard() {
  const { isRunning } = useAppStore()

  return (
    <div className="h-full">
      {isRunning ? <BattleView /> : <DeployView />}
    </div>
  )
}
