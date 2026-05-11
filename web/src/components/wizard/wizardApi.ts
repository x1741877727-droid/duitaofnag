// API client for /api/perf/* + /api/runtime/*.
// Backend: backend/api_perf_optimize.py.

import type {
  AuditResponse,
  Plan,
  ApplyTask,
  ApplyEstimate,
  RuntimeProfile,
  RuntimeMode,
} from './types'

async function jsonGet<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return res.json() as Promise<T>
}

async function jsonPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return res.json() as Promise<T>
}

export interface DetectResponse {
  hardware: AuditResponse['hardware']
  plan: Plan
}

export const wizardApi = {
  // 探测硬件 + 算 plan, 不真改
  detect: (target_count: number) =>
    jsonGet<DetectResponse>(`/api/perf/detect?target_count=${target_count}`),

  // 增量审计当前 LDPlayer 配置
  audit: (target_count: number) =>
    jsonGet<AuditResponse>(`/api/perf/audit?target_count=${target_count}`),

  // 估算 apply 总步数 + 耗时
  estimate: (target_count: number) =>
    jsonGet<ApplyEstimate>(`/api/perf/estimate?target_count=${target_count}`),

  // 一键应用 plan, 返 task_id
  apply: (target_count: number) =>
    jsonPost<{ ok: boolean; task_id: string; status: string }>(
      '/api/perf/apply',
      { target_count },
    ),

  // 轮询 apply 进度
  applyStatus: (task_id: string) =>
    jsonGet<ApplyTask>(`/api/perf/apply/${task_id}`),

  // 当前 runtime profile
  getProfile: () =>
    jsonGet<{ mode: RuntimeMode; valid_modes: RuntimeMode[]; profile: RuntimeProfile }>(
      '/api/runtime/profile',
    ),

  // 切 mode (持久化 + 通知 decision_log/vision_daemon reload)
  setMode: (mode: RuntimeMode) =>
    jsonPost<{ ok: boolean; mode: RuntimeMode; profile: RuntimeProfile }>(
      '/api/runtime/profile',
      { mode },
    ),

  // 预览某 mode 不切换
  previewPreset: (mode: RuntimeMode) =>
    jsonGet<{ mode: RuntimeMode; profile: RuntimeProfile }>(
      `/api/runtime/preset/${mode}`,
    ),
}

/** 轮询 apply 进度直到完成. 用 callback 推进度. */
export async function pollApplyUntilDone(
  task_id: string,
  on_tick: (task: ApplyTask) => void,
  intervalMs = 800,
): Promise<ApplyTask> {
  for (;;) {
    const task = await wizardApi.applyStatus(task_id)
    on_tick(task)
    if (task.status === 'done') return task
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
