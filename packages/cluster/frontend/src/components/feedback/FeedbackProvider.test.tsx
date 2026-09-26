// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { FeedbackProvider, useFeedback } from './FeedbackProvider'

afterEach(cleanup)

describe('FeedbackProvider', () => {
  it('resolves a destructive confirmation only after the user chooses an action', async () => {
    const result = vi.fn()
    function Trigger() {
      const { confirm } = useFeedback()
      return <button onClick={async () => result(await confirm({ title: 'Delete room', description: 'This removes the room for everyone.', destructive: true }))}>Delete</button>
    }
    render(<FeedbackProvider><Trigger /></FeedbackProvider>)

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/ }))
    expect(screen.getByRole('dialog', { name: 'Delete room' })).toBeInTheDocument()
    expect(result).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(result).toHaveBeenLastCalledWith(false))

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(result).toHaveBeenLastCalledWith(true))
  })
})
