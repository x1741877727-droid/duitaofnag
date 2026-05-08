/**
 * ReadyCard — 待启动态左侧主卡 (STANDBY · N READY badge + 开工 + readiness checks + 启动按钮).
 * 完全照搬 /tmp/design_dl/gameautomation/project/states.jsx ReadyCard (716-810).
 */

import { C } from '@/lib/design-tokens'

export function ReadyCard({
  greet,
  count,
  teamCount,
  emuReady,
  accel,
  onStart,
}: {
  greet: string
  count: number
  teamCount: number
  emuReady: number
  accel: 'TUN' | 'SOCKS5' | 'OFF'
  onStart: () => void
}) {
  return (
    <div
      style={{
        background: C.surface,
        borderRadius: 14,
        padding: '22px 22px 20px',
        color: C.ink,
        position: 'relative',
        overflow: 'hidden',
        border: `1px solid ${C.border}`,
        boxShadow:
          '0 1px 0 rgba(0,0,0,.02), 0 12px 32px -16px rgba(50,40,30,.08)',
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
        minHeight: 320,
      }}
    >
      {/* warm tint corner */}
      <div
        style={{
          position: 'absolute',
          top: -100,
          right: -100,
          width: 240,
          height: 240,
          background:
            'radial-gradient(circle, rgba(245,180,90,.07) 0%, transparent 65%)',
          pointerEvents: 'none',
        }}
      />

      {/* STANDBY · N READY */}
      <div
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span
          className="gb-pulse"
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: C.live,
          }}
        />
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: C.ink3,
            letterSpacing: '.1em',
            textTransform: 'uppercase',
          }}
        >
          STANDBY · {count} READY
        </span>
      </div>

      {/* greet + 开工? + sub */}
      <div style={{ position: 'relative' }}>
        <div style={{ fontSize: 13, color: C.ink3, marginBottom: 6 }}>
          {greet}, 一切准备就绪
        </div>
        <div
          style={{
            fontSize: 30,
            fontWeight: 600,
            lineHeight: 1.1,
            letterSpacing: '-.015em',
            color: C.ink,
          }}
        >
          开工 ?
        </div>
        <div
          style={{
            fontSize: 12.5,
            color: C.ink3,
            marginTop: 8,
            lineHeight: 1.55,
          }}
        >
          点击启动后约 30 秒内全部进入大厅, 队伍按大组自动配对。
        </div>
      </div>

      {/* readiness checks */}
      <div
        style={{
          position: 'relative',
          background: '#faf8f3',
          border: `1px solid ${C.borderSoft}`,
          borderRadius: 10,
          padding: '4px 12px',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {(
          [
            ['账号', `${count} 个 · ${teamCount} 队`, true],
            ['模拟器', `${emuReady} / ${count} 在跑`, emuReady === count],
            ['加速器', accel === 'OFF' ? '未启用' : `${accel} 模式`, accel !== 'OFF'],
          ] as Array<[string, string, boolean]>
        ).map(([l, v, ok], i) => (
          <div
            key={l}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '9px 0',
              borderTop: i === 0 ? 'none' : `1px solid ${C.borderSoft}`,
            }}
          >
            <span
              style={{
                width: 18,
                height: 18,
                borderRadius: 5,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: ok ? '#dcf2e1' : C.surface2,
                color: ok ? '#2f8b4a' : C.ink4,
                fontSize: 11,
                fontWeight: 700,
                flex: '0 0 18px',
              }}
            >
              {ok ? '✓' : '·'}
            </span>
            <span style={{ fontSize: 12, color: C.ink3, minWidth: 56 }}>{l}</span>
            <span
              style={{
                fontSize: 13,
                color: C.ink,
                fontWeight: 500,
                fontFamily: C.fontMono,
              }}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      {/* start button */}
      <button
        type="button"
        onClick={onStart}
        style={{
          position: 'relative',
          marginTop: 'auto',
          padding: '13px 16px',
          borderRadius: 10,
          border: `1px solid ${C.ink}`,
          background: C.ink,
          color: '#fff',
          fontWeight: 600,
          fontSize: 14,
          fontFamily: 'inherit',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          boxShadow:
            '0 1px 0 rgba(255,255,255,.08) inset, 0 4px 12px rgba(40,30,20,.12)',
        }}
      >
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}
        >
          <svg width="11" height="11" viewBox="0 0 11 11" fill="#fff">
            <path d="M2 1l8 4.5L2 10z" />
          </svg>
          开始今天的工作
        </span>
        <span
          className="gb-mono"
          style={{
            fontSize: 13,
            fontWeight: 600,
            background: 'rgba(255,255,255,.12)',
            padding: '2px 8px',
            borderRadius: 5,
          }}
        >
          {count} 台
        </span>
      </button>
    </div>
  )
}
