/**
 * FleetPills — where tasks can run, as one small pill per place in the Swarm Out header
 * (design: Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v6.html): Elastic
 * Workers (AgentCore) and each Hive (EC2), a dot in its tone (./tone.hiveTone; Elastic
 * Workers by its standby state), its state on hover. A place that cannot take a task
 * turns red or amber; a click opens the Swarm Out settings, where it is fixed. When the
 * account holds fewer Hives than the person asked for, one amber pill says so.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, type FleetStatus } from '../../services/awsAccount';
import type { SwarmOutFleet, SwarmOutFleetHive } from '../../services/swarmOut';
import { openSwarmOutSettings } from '../common/settingsEvents';
import { hiveStatusLabel } from './live';
import { hiveTone, TONE_VAR, type Tone } from './tone';
import { ELASTIC_NAME } from './where';

/** The account's Hive count, when it is short of what the person asked for. */
function useShortfall(hidden: boolean): FleetStatus | null {
  const [st, setSt] = useState<FleetStatus | null>(null);
  useEffect(() => {
    if (hidden) return undefined;
    let stop = false;
    awsAccountService.get().then((v) => { if (!stop) setSt(v.hive ?? null); }).catch(() => { /* unknown: say nothing */ });
    return () => { stop = true; };
  }, [hidden]);
  return st && (st.state === 'short' || st.state === 'cannot_build') ? st : null;
}

function placeOf(h: SwarmOutFleetHive, t: (k: string, o?: Record<string, unknown>) => string): { tone: Tone; tip: string } {
  if (h.carrier === 'agentcore') {
    return { tone: h.status === 'ready' ? 'ok' : h.status === 'failed' ? 'error' : 'attention',
      tip: t(`swarmOut.fleet.elastic.${h.status}`, { defaultValue: h.status }) };
  }
  const count = { count: h.streaming ?? 0 };
  const tip = h.status !== 'running' ? `${hiveStatusLabel(h.status)}${h.status === 'stopped' ? t('swarmOut.fleet.startsWhenNeeded') : ''}`
    : !h.reachable ? t('swarmOut.fleet.unreachable')
      : h.saturated ? t(h.memory_percent == null ? 'swarmOut.fleet.busy' : 'swarmOut.fleet.full', count)
        : [t('swarmOut.fleet.used', count), h.memory_percent != null ? t('swarmOut.fleet.memory', { percent: Math.round(h.memory_percent) }) : null].filter(Boolean).join(' · ');
  return { tone: hiveTone(h), tip };
}

export function FleetPills({ fleet, failed, hidden }: { fleet: SwarmOutFleet | null; failed: boolean; hidden: boolean }) {
  const { t } = useTranslation();
  const short = useShortfall(hidden);
  if (!fleet) return failed ? <span className="text-small text-[var(--color-text-muted)]" data-testid="so-fleet-failed">{t('swarmOut.fleet.failed')}</span> : null;
  return (
    <div className="flex flex-wrap gap-1.5 min-w-0" role="list" aria-label={t('swarmOut.board.fleetTitle')} data-testid="so-fleet">
      {fleet.hives.map((h) => {
        const p = placeOf(h, t);
        const bad = p.tone === 'error' || p.tone === 'attention';
        const elastic = h.carrier === 'agentcore';
        return (
          <span key={h.name} role="listitem" className="inline-flex"><button type="button" onClick={openSwarmOutSettings} title={p.tip}
            className="inline-flex items-center gap-1.5 max-w-[220px] pl-2 pr-2.5 py-[3px] rounded-full text-meta whitespace-nowrap"
            style={bad ? { color: TONE_VAR[p.tone], background: `color-mix(in srgb, ${TONE_VAR[p.tone]} 10%, transparent)` }
              : { color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)' }}
            data-testid="so-fleet-pill" data-tone={p.tone} data-carrier={elastic ? 'agentcore' : 'ec2'}>
            <span aria-hidden className="w-[7px] h-[7px] rounded-full flex-none" style={{ background: TONE_VAR[p.tone] }} />
            <span className="truncate">{elastic ? ELASTIC_NAME : h.name}</span>
            <span className="opacity-60">{elastic ? t('swarmOut.board.elastic') : t('swarmOut.board.ec2')}</span>
            <span className="sr-only">{p.tip}</span>
          </button></span>
        );
      })}
      {short && (
        <span role="listitem" className="inline-flex"><button type="button" onClick={openSwarmOutSettings} data-testid="so-fleet-short"
          className="inline-flex items-center px-2.5 py-[3px] rounded-full text-meta whitespace-nowrap"
          style={{ color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 10%, transparent)` }}>
          {short.state === 'cannot_build' ? t('swarmOut.fleet.cannotBuild') : t('swarmOut.fleet.short', { count: short.short ?? 0 })}
        </button></span>
      )}
    </div>
  );
}
