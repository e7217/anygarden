import { useMemo } from 'react'
import type { NodeKind } from './types'
import { BORDER, TEXT_MUTED, TEXT_PRIMARY } from './constants'

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
  const kindList = useMemo<NodeKind[]>(() => ['user', 'machine', 'agent', 'room'], [])

  return (
    <aside
      style={{
        width: 240,
        flex: '0 0 240px',
        borderRight: '1px solid rgba(0,0,0,0.1)',
        background: 'var(--color-surface)',
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        overflowY: 'auto',
      }}
      aria-label="Topology filters"
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
          Search
        </label>
        <input
          type="search"
          placeholder="Filter by name..."
          value={filter.search}
          onChange={e => onChange({ ...filter, search: e.target.value })}
          style={{
            width: '100%',
            padding: '6px 10px',
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
          Node types
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
                padding: '2px 0',
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
              <span style={{ flex: 1, textTransform: 'capitalize' }}>{kind}</span>
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
            Agent engine
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
            Agent state
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
  const active = new Set(selected ?? options)
  const allSelected = selected === null || selected.length === options.length
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      <Chip
        label="All"
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
        padding: '4px 8px',
        borderRadius: 9999,
        background: active ? 'var(--color-brand-tint-bg)' : 'rgba(0,0,0,0.05)',
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
