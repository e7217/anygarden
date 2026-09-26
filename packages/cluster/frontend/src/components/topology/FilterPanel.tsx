import { useMemo } from 'react'
import type { NodeKind } from './types'
import { BORDER, TEXT_MUTED, TEXT_PRIMARY } from './constants'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

const kindKeys: Record<NodeKind, MessageKey> = {
  user: 'topology.kindUser',
  machine: 'topology.kindMachine',
  agent: 'topology.kindAgent',
  room: 'topology.kindRoom',
  project: 'topology.kindProject',
}

export interface FilterState {
  kinds: Record<NodeKind, boolean>
  engines: string[] | null // null = all
  actualStates: string[] | null // null = all
  search: string
}

export const DEFAULT_FILTER: FilterState = {
  kinds: {
    user: true,
    machine: true,
    agent: true,
    room: true,
    project: false,
  },
  engines: null,
  actualStates: null,
  search: '',
}

interface Props {
  filter: FilterState
  onChange: (f: FilterState) => void
  counts: Record<NodeKind, number>
  knownEngines: string[]
  knownStates: string[]
}

/**
 * Left filter rail — node-kind toggles, engine chips, state chips,
 * name search. 240px wide on desktop, collapses to a drawer on
 * mobile (handled by the parent page).
 */
export default function FilterPanel({
  filter,
  onChange,
  counts,
  knownEngines,
  knownStates,
}: Props) {
  const { t } = useLocale()
  const kindList = useMemo<NodeKind[]>(() => ['user', 'machine', 'agent', 'room'], [])

  return (
    <aside
      className="topology-filter-panel"
      style={{
        width: 240,
        flex: '0 0 240px',
        minHeight: 0,
        borderRight: BORDER,
        background: 'var(--color-surface)',
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        overflowY: 'auto',
      }}
      aria-label={t('topology.filters')}
    >
      <div>
        <label
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: 0.125,
            textTransform: 'uppercase',
            color: TEXT_MUTED,
            display: 'block',
            marginBottom: 8,
          }}
        >
          {t('topology.search')}
        </label>
        <input
          type="search"
          placeholder={t('topology.filterByName')}
          aria-label={t('topology.search')}
          value={filter.search}
          onChange={e => onChange({ ...filter, search: e.target.value })}
          style={{
            width: '100%',
            padding: '10px 12px',
            minHeight: 44,
            border: BORDER,
            borderRadius: 4,
            fontSize: 13,
            color: TEXT_PRIMARY,
            background: 'var(--color-surface)',
          }}
        />
      </div>

      <div>
        <p
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: 0.125,
            textTransform: 'uppercase',
            color: TEXT_MUTED,
            margin: '0 0 8px',
          }}
        >
          {t('topology.nodeTypes')}
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {kindList.map(kind => (
            <label
              key={kind}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                color: TEXT_PRIMARY,
                cursor: 'pointer',
                minHeight: 40,
              }}
            >
              <input
                type="checkbox"
                checked={filter.kinds[kind]}
                onChange={e =>
                  onChange({
                    ...filter,
                    kinds: { ...filter.kinds, [kind]: e.target.checked },
                  })
                }
              />
              <span style={{ flex: 1 }}>{t(kindKeys[kind])}</span>
              <span style={{ fontSize: 11, color: TEXT_MUTED }}>
                {counts[kind] ?? 0}
              </span>
            </label>
          ))}
        </div>
      </div>

      {knownEngines.length > 0 && (
        <div>
          <p
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 0.125,
              textTransform: 'uppercase',
              color: TEXT_MUTED,
              margin: '0 0 8px',
            }}
          >
            {t('topology.agentEngine')}
          </p>
          <ChipGroup
            options={knownEngines}
            selected={filter.engines}
            onChange={v => onChange({ ...filter, engines: v })}
          />
        </div>
      )}

      {knownStates.length > 0 && (
        <div>
          <p
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 0.125,
              textTransform: 'uppercase',
              color: TEXT_MUTED,
              margin: '0 0 8px',
            }}
          >
            {t('topology.agentState')}
          </p>
          <ChipGroup
            options={knownStates}
            selected={filter.actualStates}
            onChange={v => onChange({ ...filter, actualStates: v })}
          />
        </div>
      )}
    </aside>
  )
}

function ChipGroup({
  options,
  selected,
  onChange,
}: {
  options: string[]
  selected: string[] | null
  onChange: (v: string[] | null) => void
}) {
  const { t } = useLocale()
  const active = new Set(selected ?? options)
  const allSelected = selected === null || selected.length === options.length
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      <Chip
        label={t('topology.all')}
        active={allSelected}
        onClick={() => onChange(null)}
      />
      {options.map(opt => (
        <Chip
          key={opt}
          label={opt}
          active={!allSelected && active.has(opt)}
          onClick={() => {
            if (allSelected) {
              onChange([opt])
            } else {
              const next = new Set(selected ?? [])
              if (next.has(opt)) {
                next.delete(opt)
              } else {
                next.add(opt)
              }
              const arr = [...next]
              onChange(arr.length === 0 ? null : arr)
            }
          }}
        />
      ))}
    </div>
  )
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        fontSize: 12,
        fontWeight: 600,
        letterSpacing: 0.125,
        padding: '8px 10px',
        minHeight: 36,
        borderRadius: 9999,
        background: active ? 'var(--color-brand-tint-bg)' : 'var(--color-surface-alt)',
        color: active ? 'var(--color-brand-tint-text)' : TEXT_PRIMARY,
        border: 'none',
        cursor: 'pointer',
        textTransform: 'capitalize',
      }}
    >
      {label}
    </button>
  )
}
