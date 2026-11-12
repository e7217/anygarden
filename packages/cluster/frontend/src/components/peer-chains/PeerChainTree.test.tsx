// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PeerChainTree from './PeerChainTree'
import PeerMessagePath from './PeerMessagePath'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import { messagePlacements } from '@/lib/peerChains'
import { node, sampleChain } from '@/lib/peerChains.fixtures'

vi.mock('./PeerChainGraph', () => ({ default: () => <div data-testid="peer-chain-graph" /> }))

beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

function renderTree(props: Partial<Parameters<typeof PeerChainTree>[0]> = {}) {
  return render(
    <LocaleProvider>
      <PeerChainTree chain={sampleChain()} selectedNodeId={null} onSelect={vi.fn()} {...props} />
    </LocaleProvider>,
  )
}

const shownRows = () =>
  Array.from(screen.getByTestId('peer-chain-rows').querySelectorAll('[data-node-id]')).map(row => row.getAttribute('data-node-id'))

describe('PeerChainTree', () => {
  it('shows the active path expanded and folds finished branches, with state as text', () => {
    renderTree()
    expect(shownRows()).toEqual(['B', 'D', 'E', 'F', 'G'])
    const row = screen.getByTestId('peer-chain-rows').querySelector('[data-node-id="G"]') as HTMLElement
    expect(within(row).getByText('G')).toBeInTheDocument()
    expect(within(row).getByText('Answering')).toBeInTheDocument()
  })

  it('expands a folded branch on request', () => {
    renderTree()
    fireEvent.click(screen.getByRole('button', { name: 'Show questions asked by B' }))
    expect(shownRows()).toEqual(['B', 'C', 'D', 'E', 'F', 'G'])
  })

  it('filters the thread to a branch and resets from the root row', () => {
    const onSelect = vi.fn()
    renderTree({ onSelect })
    fireEvent.click(screen.getByRole('button', { name: /^Show only messages of A → D/ }))
    expect(onSelect).toHaveBeenLastCalledWith('D')
    fireEvent.click(screen.getByRole('button', { name: 'Show all: A · Gathering answers', pressed: true }))
    expect(onSelect).toHaveBeenLastCalledWith(null)
  })

  it('offers stop only while the chain runs and the viewer may stop it', () => {
    const { unmount } = renderTree({ onCancel: vi.fn() })
    expect(screen.getByRole('button', { name: 'Stop every agent in this question chain' })).toBeInTheDocument()
    unmount()
    renderTree({ chain: sampleChain({ state: 'done' }), onCancel: vi.fn() })
    expect(screen.queryByRole('button', { name: 'Stop every agent in this question chain' })).toBeNull()
  })

  it('names only the answerer on a row whose asker is the row above, keeping the full path for assistive tech', () => {
    renderTree()
    const rows = screen.getByTestId('peer-chain-rows')
    expect(rows.querySelector('[data-node-id="E"] [data-testid="peer-chain-row-label"]')).toHaveTextContent(/^E$/)
    expect(screen.getByRole('button', { name: /^Show only messages of D → E/ })).toBeInTheDocument()
  })

  it('keeps the asker on a row whose parent question is missing', () => {
    renderTree({ chain: sampleChain({ nodes: [node({ id: 'Y', parent_id: 'missing', caller_name: 'X' })] }) })
    const rows = screen.getByTestId('peer-chain-rows')
    expect(rows.querySelector('[data-node-id="Y"] [data-testid="peer-chain-row-label"]')).toHaveTextContent('X → Y')
  })

  it('lines every row up on one toggle column, or none when nothing folds', () => {
    const slots = () => screen.getByTestId('peer-chain-rows').querySelectorAll('[data-toggle-slot]').length
    const { unmount } = renderTree()
    // Root plus the five shown rows each reserve the column.
    expect(slots()).toBe(6)
    unmount()
    renderTree({ chain: sampleChain({ nodes: [node({ id: 'B' }), node({ id: 'D' })] }) })
    expect(slots()).toBe(0)
  })

  it('switches to the graph view', async () => {
    renderTree()
    fireEvent.click(screen.getByRole('button', { name: 'Graph' }))
    expect(await screen.findByTestId('peer-chain-graph')).toBeInTheDocument()
  })
})

describe('PeerMessagePath', () => {
  it('labels a deep question with an abbreviated path and capped indent', () => {
    const placement = messagePlacements([sampleChain()]).get('q-G')!
    render(
      <LocaleProvider>
        <PeerMessagePath placement={placement}><p>body</p></PeerMessagePath>
      </LocaleProvider>,
    )
    expect(screen.getByTestId('peer-message-label')).toHaveTextContent('… › E › F → G')
    expect(screen.getByTestId('peer-message')).toHaveAttribute('data-peer-indent', '3')
    expect(screen.getByText('Question A › D › E › F › G')).toHaveClass('sr-only')
  })
})
