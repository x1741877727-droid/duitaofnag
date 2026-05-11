/**
 * /api/perf/* 客户端封装. 跟 archiveApi / templatesApi 同模式.
 *
 * 后端: backend/api_perf_optimize.py
 */

export interface HardwareInfo {
  cpu_name: string
  cpu_sockets: number
  cpu_physical: number
  cpu_logical: number
  cpu_has_ht: boolean
  ram_gb: number
  ram_free_gb: number
  gpu_name: string
  gpu_vendor: string
  disk_type: string
  ldplayer_path: string
  instance_count_existing: number
  instance_indexes: number[]
}

export interface OptimizePlan {
  instance_count: number
  ram_per_inst_mb: number
  cpu_per_inst: number
  threads_per_inst: number
  masks: number[]
  startup_interval_s: number
  gpu_mode_recommendation: string
  warnings: string[]
  fits_target: boolean
}

export interface AppliedChange {
  category: string
  label: string
  detail?: string
}

export interface AppliedResult {
  success: boolean
  error?: string | null
  duration_s: number
  changes: AppliedChange[]
  skipped: string[]
  hardware: HardwareInfo
  plan: OptimizePlan
}

export interface PerfStatus {
  optimized: boolean
  signature_state: null | 'matched' | 'changed'
  last_applied_at: string | null
  current_signature: string
}

export interface PerfDetectResponse {
  hardware: HardwareInfo
  plan: OptimizePlan
}

export interface PerfTaskStatus {
  task_id: string
  status: 'running' | 'done'
  progress: Array<{ step_id: string; message: string; ts: number }>
  result: AppliedResult | null
  elapsed_s: number
}

export async function getPerfStatus(): Promise<PerfStatus> {
  const r = await fetch('/api/perf/status', { cache: 'no-store' })
  return r.json()
}

export async function detectAndPlan(targetCount = 12): Promise<PerfDetectResponse> {
  const r = await fetch(`/api/perf/detect?target_count=${targetCount}`, { cache: 'no-store' })
  return r.json()
}

export async function applyPlan(targetCount = 12): Promise<{ task_id: string }> {
  const r = await fetch('/api/perf/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_count: targetCount }),
  })
  return r.json()
}

export async function pollApplyStatus(taskId: string): Promise<PerfTaskStatus> {
  const r = await fetch(`/api/perf/apply/${taskId}?_t=${Date.now()}`, {
    cache: 'no-store',
    headers: { 'Cache-Control': 'no-cache' },
  })
  return r.json()
}
