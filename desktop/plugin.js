/**
 * Cache Meter - pi-style cache/token/cost chip for the Hermes desktop status bar.
 *
 * Shows:  ↑input ↓output R cache-read W cache-write CH hit-rate% $cost
 * Convention matches badlogic's pi: prompt volume = input + cacheRead + cacheWrite,
 * where `input` EXCLUDES cached tokens. Stats come from this plugin's own backend
 * (dashboard/plugin_api.py) reading state.db directly, so no Hermes core patch is
 * required. Enable the plugin's Python side once:
 *     hermes plugins enable cache-meter
 *
 * Data sources:
 *   - ctx.rest('/usage/<id>')   current session (live DB read, exact mid-turn)
 *   - host.request('session.usage') fallback fields (context %, live counters)
 */

import { cn, haptic, host, Tip, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx } from 'react/jsx-runtime'

const ID = 'cache-meter'
const POLL_MS = 5000

function fmtTok(n) {
  const v = Number(n) || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}k`
  return String(v)
}

function fmtCost(usd) {
  if (!(usd > 0)) return ''
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`
}

async function fetchUsage(rest, sessionId) {
  // Primary: our backend reads the stored session row (works on stock Hermes,
  // no gateway restart needed after install; updated at most ~a turn behind).
  try {
    const u = await rest(`/usage/${encodeURIComponent(sessionId)}`)
    if (u && !u.error) return u
  } catch {
    /* backend disabled or older mount - fall through */
  }
  // Fallback: gateway RPC (only has cache fields on patched builds).
  try {
    const u = await host.request('session.usage', { session_id: sessionId })
    if (u && typeof u === 'object' && !u.error) return u
  } catch {
    /* detached session etc. */
  }
  return null
}

function CacheMeterChip({ rest }) {
  const sessionId = useValue(host.state.activeSessionId)
  const [usage, setUsage] = useState(null)

  useEffect(() => {
    if (!sessionId) return undefined
    let alive = true
    const tick = () => fetchUsage(rest, sessionId).then(u => { if (alive) setUsage(u) })
    tick()
    const iv = setInterval(tick, POLL_MS)
    return () => { alive = false; clearInterval(iv) }
  }, [rest, sessionId])

  if (!sessionId || !usage) return null

  const parts = []
  if (usage.input) parts.push(`↑${fmtTok(usage.input)}`)
  if (usage.output) parts.push(`↓${fmtTok(usage.output)}`)
  if (usage.cache_read) parts.push(`R${fmtTok(usage.cache_read)}`)
  if (usage.cache_write) parts.push(`W${fmtTok(usage.cache_write)}`)
  if (usage.cache_hit_rate != null) parts.push(`CH ${Number(usage.cache_hit_rate).toFixed(1)}%`)
  const cost = fmtCost(usage.cost_usd)
  if (cost) parts.push(cost)
  if (!parts.length) return null

  const promptVolume =
    (Number(usage.input) || 0) + (Number(usage.cache_read) || 0) + (Number(usage.cache_write) || 0)
  const ctxPct = usage.context_percent != null ? Math.max(0, Math.min(100, Math.round(usage.context_percent))) : null

  const tipLines = [
    `↑ uncached input    ${fmtTok(usage.input)}`,
    `↓ output            ${fmtTok(usage.output)}`,
    `R served from cache ${fmtTok(usage.cache_read)}`,
    `W written to cache  ${fmtTok(usage.cache_write)}`,
    `prompt volume       ${fmtTok(promptVolume)}`,
    usage.cache_hit_rate != null
      ? `CH hit rate         ${Number(usage.cache_hit_rate).toFixed(1)}%`
      : 'CH hit rate         (no cache activity yet)',
    cost ? `cost               ${cost}${usage.cost_source === 'actual' ? ' (actual)' : ''}` : null,
    ctxPct != null ? `context            ${ctxPct}% (${fmtTok(usage.context_used ?? 0)}/${fmtTok(usage.context_max ?? 0)})` : null,
    `api calls           ${usage.calls ?? 0}`
  ].filter(Boolean)

  return jsx(Tip, {
    label: tipLines.join('\n'),
    children: jsx('button', {
      className: cn(
        'inline-flex h-full max-w-[26rem] items-center gap-1.5 overflow-hidden px-1.5 text-[0.6875rem] transition-colors',
        'tabular-nums text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      type: 'button',
      onClick: () => {
        haptic('tap')
        host.notify({ kind: 'info', message: `Cache meter\n${tipLines.join('\n')}` })
      },
      children: jsx('span', { className: 'whitespace-nowrap', children: parts.join(' ') })
    })
  })
}

export default {
  id: ID,
  name: 'Cache Meter',
  register(ctx) {
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 140,
      render: () => jsx(CacheMeterChip, { rest: ctx.rest })
    })
  }
}
