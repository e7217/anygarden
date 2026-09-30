// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import SearchDialog from './SearchDialog'

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => [] })),
}))

afterEach(cleanup)

function renderDialog(open = true, onClose = vi.fn()) {
  render(
    <MemoryRouter>
      <SearchDialog open={open} onClose={onClose} />
    </MemoryRouter>,
  )
  return onClose
}

describe('SearchDialog', () => {
  it('renders as a modal dialog with a named, focused search input', () => {
    renderDialog()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName()
    const input = screen.getByRole('textbox')
    expect(input).toHaveAccessibleName()
    expect(input).toHaveFocus()
  })

  it('closes on Escape', () => {
    const onClose = renderDialog()
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('renders nothing when closed', () => {
    renderDialog(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
