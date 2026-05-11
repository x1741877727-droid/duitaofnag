// 跟 backend/automation/perf_optimizer.py + runtime_profile.py schema 对齐.
// 任何字段改动两边一起改.

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

export interface Plan {
  instance_count: number
  ram_per_inst_mb: number
  cpu_per_inst: number
  resolution_w: number
  resolution_h: number
  dpi: number
  threads_per_inst: number
  masks: number[]
  startup_interval_s: number
  gpu_mode_recommendation: string
  tier: 'optimal' | 'balanced' | 'marginal' | 'stretched' | string
  warnings: string[]
  fits_target: boolean
  estimated_crash_rate_pct: number
}

export interface AuditItem {
  key: string
  label: string
  expected: string
  actual: string
  status: 'applied' | 'missing' | 'drift' | 'below_recommended'
  auto_fixable: boolean
}

export interface AuditReport {
  applied: AuditItem[]
  missing: AuditItem[]
  drift: AuditItem[]
  below_recommended: AuditItem[]
  instance_indexes_audited: number[]
  notes: string[]
  needs_action: boolean
  total_items: number
}

export interface AuditResponse {
  hardware: HardwareInfo
  expected_plan: Plan
  report: AuditReport
}

export interface AppliedChange {
  category: string
  label: string
  detail: string
}

export interface ApplyProgress {
  step_id: string
  message: string
  ts: number
}

export interface ApplyResult {
  success: boolean
  error: string | null
  duration_s: number
  changes: AppliedChange[]
  skipped: string[]
  hardware: HardwareInfo
  plan: Plan
}

export interface ApplyEstimate {
  total_steps: number
  est_seconds: number
  will_run: string[]
  will_skip: string[]
}

export interface ApplyTask {
  task_id: string
  status: 'running' | 'done'
  progress: ApplyProgress[]
  result: ApplyResult | null
  elapsed_s: number
  estimate: ApplyEstimate | null
}

export type RuntimeMode = 'stable' | 'balanced' | 'speed'

export interface RuntimeProfile {
  mode: RuntimeMode
  pool_max_workers: number
  dlog_pool_workers: number
  p0_round_interval: number
  p1_round_interval: number
  p2_round_interval: number
  p3_round_interval: number
  p4_round_interval: number
  p5_round_interval: number
  daemon_target_fps: number
  daemon_motion_threshold: number
  p1_motion_threshold: number
  five_tier_short_circuit: boolean
  tmpl_conf_default: number
  yolo_conf: number
  ocr_pool_divisor: number
  ocr_pool_min: number
  ocr_pool_max: number
}

export type WizardStep = 'detect' | 'choose' | 'recommend' | 'apply' | 'done'
