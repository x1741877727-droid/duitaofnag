import { useEffect, useState } from "react";
import { useAppStore } from "../stores/appStore";

interface PipelineResult {
  ok: boolean;
  latency?: {
    screenshot_ms: number;
    template_ms: number;
    ocr_ms: number;
    llm_state_ms: number;
    llm_popup_ms: number;
    total_ms: number;
  };
  template?: { matched: number; total: number };
  ocr?: { text_count: number; full_text: string };
  llm_state?: any;
  llm_popup?: any;
  error?: string;
}

interface DebugState {
  running: boolean;
  paused?: boolean;
  instances?: Record<
    string,
    {
      state: string;
      role: string;
      group: string;
      state_duration: number;
      available_triggers: string[];
      is_error: boolean;
      error_msg: string;
    }
  >;
}

export function DebugPanel() {
  const running = useAppStore((s) => s.running);
  const [instanceIdx, setInstanceIdx] = useState(0);
  const [screenshotUrl, setScreenshotUrl] = useState("");
  const [pipelineResult, setPipelineResult] = useState<PipelineResult | null>(null);
  const [llmResult, setLlmResult] = useState<any>(null);
  const [ocrResult, setOcrResult] = useState<any>(null);
  const [debugState, setDebugState] = useState<DebugState | null>(null);
  const [loading, setLoading] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);

  // 自动刷新状态
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(refreshState, 1000);
    return () => clearInterval(id);
  }, [autoRefresh]);

  async function refreshScreenshot() {
    setScreenshotUrl(`/api/screenshot/${instanceIdx}?t=${Date.now()}`);
  }

  async function refreshState() {
    const r = await fetch("/api/debug/state");
    setDebugState(await r.json());
  }

  async function testLLM(promptKey: string) {
    setLoading("llm");
    const r = await fetch(
      `/api/debug/test-llm?instance_index=${instanceIdx}&prompt_key=${promptKey}`,
      { method: "POST" }
    );
    setLlmResult(await r.json());
    setLoading("");
  }

  async function testOCR() {
    setLoading("ocr");
    const r = await fetch(`/api/debug/test-ocr?instance_index=${instanceIdx}`, {
      method: "POST",
    });
    setOcrResult(await r.json());
    setLoading("");
  }

  async function testPipeline() {
    setLoading("pipeline");
    const r = await fetch(
      `/api/debug/test-pipeline?instance_index=${instanceIdx}`,
      { method: "POST" }
    );
    setPipelineResult(await r.json());
    setLoading("");
  }

  async function stepTrigger(trigger: string) {
    setLoading("step");
    const r = await fetch(
      `/api/debug/step?instance_index=${instanceIdx}&trigger=${trigger}`,
      { method: "POST" }
    );
    const data = await r.json();
    if (!data.ok) alert(data.error);
    await refreshState();
    setLoading("");
  }

  const inst = debugState?.instances?.[String(instanceIdx)];

  const btnCls =
    "px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40";

  return (
    <div className="p-5 space-y-4 max-w-5xl">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-bold">调试面板</h2>
        {!running && (
          <span className="text-xs text-yellow-400">需要先点「全部启动」</span>
        )}
      </div>

      {/* 实例选择 */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-slate-400">选择实例:</span>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <button
            key={i}
            className={`px-3 py-1 text-xs rounded ${
              instanceIdx === i
                ? "bg-blue-600 text-white"
                : "bg-slate-700 text-slate-300"
            }`}
            onClick={() => setInstanceIdx(i)}
          >
            {i}
          </button>
        ))}
        <label className="flex items-center gap-1 text-xs text-slate-400 ml-4">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          自动刷新状态 (1s)
        </label>
      </div>

      {/* 实例状态 */}
      {inst && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-slate-500">状态:</span>{" "}
              <span className="font-mono text-emerald-400">{inst.state}</span>
              {" "}
              <span className="text-slate-500">({inst.state_duration}s)</span>
            </div>
            <div>
              <span className="text-slate-500">角色:</span>{" "}
              <span>{inst.group}/{inst.role}</span>
            </div>
            {inst.error_msg && (
              <div className="col-span-2 text-red-400">
                错误: {inst.error_msg}
              </div>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            <span className="text-xs text-slate-500 mr-2">可触发:</span>
            {inst.available_triggers.map((t) => (
              <button
                key={t}
                className="px-2 py-0.5 text-xs rounded bg-purple-700 hover:bg-purple-600 text-white"
                onClick={() => stepTrigger(t)}
                disabled={loading === "step"}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 截图预览 */}
      <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">实时截图</h3>
          <button className={btnCls} onClick={refreshScreenshot}>
            刷新截图
          </button>
        </div>
        {screenshotUrl ? (
          <img
            src={screenshotUrl}
            alt="screenshot"
            className="w-full max-w-2xl rounded border border-slate-700"
          />
        ) : (
          <div className="text-xs text-slate-500">点击「刷新截图」查看</div>
        )}
      </div>

      {/* 完整管道测试 */}
      <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">完整管道测试 (一键测延迟)</h3>
          <button
            className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40"
            onClick={testPipeline}
            disabled={loading === "pipeline"}
          >
            {loading === "pipeline" ? "测试中..." : "运行完整测试"}
          </button>
        </div>
        {pipelineResult && (
          <div className="text-xs space-y-2">
            {pipelineResult.error && (
              <div className="text-red-400">{pipelineResult.error}</div>
            )}
            {pipelineResult.latency && (
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <span className="text-slate-500">截图:</span>{" "}
                  <span className="font-mono">{pipelineResult.latency.screenshot_ms}ms</span>
                </div>
                <div>
                  <span className="text-slate-500">模板:</span>{" "}
                  <span className="font-mono">{pipelineResult.latency.template_ms}ms</span>
                </div>
                <div>
                  <span className="text-slate-500">OCR:</span>{" "}
                  <span className="font-mono">{pipelineResult.latency.ocr_ms}ms</span>
                </div>
                <div>
                  <span className="text-slate-500">LLM 状态:</span>{" "}
                  <span className="font-mono">{pipelineResult.latency.llm_state_ms}ms</span>
                </div>
                <div>
                  <span className="text-slate-500">LLM 弹窗:</span>{" "}
                  <span className="font-mono">{pipelineResult.latency.llm_popup_ms}ms</span>
                </div>
                <div>
                  <span className="text-slate-500">总计:</span>{" "}
                  <span className="font-mono font-bold text-yellow-400">
                    {pipelineResult.latency.total_ms}ms
                  </span>
                </div>
              </div>
            )}
            {pipelineResult.template && (
              <div>
                <span className="text-slate-500">模板匹配:</span>{" "}
                {pipelineResult.template.matched}/{pipelineResult.template.total}
              </div>
            )}
            {pipelineResult.ocr && (
              <div>
                <span className="text-slate-500">OCR 文字 ({pipelineResult.ocr.text_count} 条):</span>{" "}
                <span className="text-slate-300">{pipelineResult.ocr.full_text}</span>
              </div>
            )}
            {pipelineResult.llm_state && (
              <div>
                <span className="text-slate-500">LLM 状态:</span>{" "}
                <span className="text-cyan-400">
                  {JSON.stringify(pipelineResult.llm_state)}
                </span>
              </div>
            )}
            {pipelineResult.llm_popup && (
              <div>
                <span className="text-slate-500">LLM 弹窗:</span>{" "}
                <span className="text-cyan-400">
                  {JSON.stringify(pipelineResult.llm_popup)}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 单项测试 */}
      <div className="grid grid-cols-2 gap-3">
        {/* LLM 测试 */}
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
          <h3 className="text-sm font-semibold mb-2">LLM 测试</h3>
          <div className="flex flex-wrap gap-1 mb-2">
            <button className={btnCls} onClick={() => testLLM("detect_popup")} disabled={loading === "llm"}>
              检测弹窗
            </button>
            <button className={btnCls} onClick={() => testLLM("analyze_state")} disabled={loading === "llm"}>
              分析状态
            </button>
            <button className={btnCls} onClick={() => testLLM("read_opponent")} disabled={loading === "llm"}>
              读对手
            </button>
          </div>
          {llmResult && (
            <div className="text-xs space-y-1">
              <div>
                <span className="text-slate-500">耗时:</span>{" "}
                <span className="font-mono text-yellow-400">
                  {llmResult.llm_ms ?? llmResult.latency_ms}ms
                </span>
              </div>
              {llmResult.error && (
                <div className="text-red-400">{llmResult.error}</div>
              )}
              <pre className="text-xs text-slate-300 whitespace-pre-wrap break-all bg-slate-900 p-2 rounded mt-1 max-h-40 overflow-auto">
                {JSON.stringify(llmResult.response, null, 2)}
              </pre>
            </div>
          )}
        </div>

        {/* OCR 测试 */}
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
          <h3 className="text-sm font-semibold mb-2">OCR 测试</h3>
          <button className={btnCls} onClick={testOCR} disabled={loading === "ocr"}>
            {loading === "ocr" ? "识别中..." : "运行 OCR"}
          </button>
          {ocrResult && (
            <div className="text-xs space-y-1 mt-2">
              <div>
                <span className="text-slate-500">耗时:</span>{" "}
                <span className="font-mono text-yellow-400">{ocrResult.ocr_ms}ms</span>
                {" "}
                <span className="text-slate-500">| 识别:</span>{" "}
                <span>{ocrResult.text_count} 条</span>
              </div>
              <pre className="text-xs text-slate-300 whitespace-pre-wrap bg-slate-900 p-2 rounded max-h-40 overflow-auto">
                {ocrResult.full_text}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
