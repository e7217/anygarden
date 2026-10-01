// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import NotFoundPage from './NotFoundPage'

afterEach(cleanup)

function renderAt(path: string) {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<p>home page</p>} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </MemoryRouter>
    </LocaleProvider>,
  )
}

describe('NotFoundPage (#773)', () => {
  it('explains the missing page instead of rendering nothing', () => {
    renderAt('/nonexistent-page')
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.getByText('/nonexistent-page')).toBeInTheDocument()
  })

  it('offers a way back home', () => {
    renderAt('/nonexistent-page')
    fireEvent.click(screen.getByRole('link'))
    expect(screen.getByText('home page')).toBeInTheDocument()
  })
})
