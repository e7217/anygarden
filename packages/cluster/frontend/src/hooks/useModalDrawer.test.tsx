// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { useRef, useState } from 'react'
import { useModalDrawer } from './useModalDrawer'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

function Example() {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLElement>(null)
  useModalDrawer({ open, onClose: () => setOpen(false), panelRef, desktopMinWidth: 2000 })
  return <>
    <main data-testid="background"><button onClick={() => setOpen(true)}>Open navigation</button><button>Background action</button></main>
    <div inert data-testid="already-inert">Existing disabled region</div>
    <aside ref={panelRef} id="test-drawer" hidden={!open} tabIndex={-1}>
      <button data-drawer-close onClick={() => setOpen(false)}>Close navigation</button>
      <Dialog><DialogTrigger asChild><button>Edit details</button></DialogTrigger><DialogContent aria-describedby={undefined}><DialogTitle>Details</DialogTitle><input aria-label="Detail name" /></DialogContent></Dialog>
      <button>Last navigation action</button>
    </aside>
  </>
}

afterEach(cleanup)
function openDrawer() {
  render(<Example />)
  const trigger = screen.getByRole('button', { name: 'Open navigation' })
  trigger.focus()
  fireEvent.click(trigger)
  return trigger
}

describe('useModalDrawer', () => {
  it('focuses the drawer, disables the background, and wraps keyboard traversal', () => {
    const trigger = openDrawer()
    const close = screen.getByRole('button', { name: 'Close navigation' })
    const last = screen.getByRole('button', { name: 'Last navigation action' })
    expect(close).toHaveFocus()
    expect(screen.getByTestId('background')).toHaveAttribute('inert')
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(close).toHaveFocus()
    trigger.focus()
    expect(close).toHaveFocus()
  })

  it('closes on Escape, restores focus, and preserves regions already disabled', async () => {
    const trigger = openDrawer()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: 'Close navigation' })).not.toBeInTheDocument()
    expect(screen.getByTestId('background')).not.toHaveAttribute('inert')
    expect(screen.getByTestId('already-inert')).toHaveAttribute('inert')
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('lets a nested Radix dialog own focus and handle the first Escape', async () => {
    openDrawer()
    const editor = screen.getByRole('button', { name: 'Edit details' })
    fireEvent.click(editor)
    const input = await screen.findByRole('textbox', { name: 'Detail name' })
    input.focus()
    expect(input).toHaveFocus()
    fireEvent.keyDown(input, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Details' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Close navigation' })).toBeInTheDocument()
    await waitFor(() => expect(editor).toHaveFocus())
  })
})
