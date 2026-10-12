// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomSettingsMenu from './RoomSettingsMenu'

afterEach(() => cleanup())

describe('RoomSettingsMenu — leave room', () => {
  it('[ROOM-05] shows a leave action that calls the handler', () => {
    const onLeaveRoom = vi.fn()
    render(<RoomSettingsMenu onLeaveRoom={onLeaveRoom} />)
    fireEvent.click(screen.getByTestId('room-header-settings-menu-trigger'))
    const item = screen.getByTestId('room-menu-leave')
    expect(item).toHaveTextContent(/leave room/i)
    fireEvent.click(item)
    expect(onLeaveRoom).toHaveBeenCalledTimes(1)
    // Menu closes after picking.
    expect(screen.queryByTestId('room-menu-leave')).not.toBeInTheDocument()
  })

  it('[ROOM-05] hides the leave action when no handler is passed (e.g. DM rooms)', () => {
    render(<RoomSettingsMenu onEditRoom={vi.fn()} />)
    fireEvent.click(screen.getByTestId('room-header-settings-menu-trigger'))
    expect(screen.queryByTestId('room-menu-leave')).not.toBeInTheDocument()
  })
})
