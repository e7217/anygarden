// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

import RespondingIndicator from './RespondingIndicator'

afterEach(() => cleanup())

describe('RespondingIndicator', () => {
  it('is announced as responding and drawn as three dots, not one [MSG-09]', () => {
    render(<RespondingIndicator />)
    const indicator = screen.getByRole('status', { name: 'Responding' })
    expect(indicator).toHaveAttribute('title', 'Responding')
    expect(indicator.querySelectorAll('.responding-dot')).toHaveLength(3)
  })
})
