// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SidebarExpandButton from './SidebarExpandButton'

const toggleCollapsedSpy = vi.fn()
const mockSidebarLayout = vi.fn(() => ({
  collapsed: false,
  toggleCollapsed: toggleCollapsedSpy,
  setCollapsed: vi.fn(),
}))
vi.mock('@/hooks/useSidebarLayout', () => ({
  useSidebarLayout: () => mockSidebarLayout(),
}))

beforeEach(() => {
  toggleCollapsedSpy.mockReset()
  mockSidebarLayout.mockReset()
  mockSidebarLayout.mockReturnValue({
    collapsed: false,
    toggleCollapsed: toggleCollapsedSpy,
    setCollapsed: vi.fn(),
  })
})

afterEach(() => cleanup())

describe('SidebarExpandButton', () => {
  it('renders nothing when the sidebar is expanded', () => {
    render(<SidebarExpandButton />)
    expect(screen.queryByTestId('sidebar-expand')).toBeNull()
  })

  it('renders the floating button when the sidebar is collapsed', () => {
    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
    })
    render(<SidebarExpandButton />)
    const btn = screen.getByTestId('sidebar-expand')
    expect(btn).toHaveAttribute('aria-label', 'Expand sidebar')
  })

  it('invokes toggleCollapsed when clicked', () => {
    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
    })
    render(<SidebarExpandButton />)
    fireEvent.click(screen.getByTestId('sidebar-expand'))
    expect(toggleCollapsedSpy).toHaveBeenCalledTimes(1)
  })
})
