// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { LocaleProvider, useLocale } from '@/i18n/LocaleProvider'
import FilterPanel, { DEFAULT_FILTER } from './FilterPanel'

afterEach(() => cleanup())

function Harness() {
  const { setLocale } = useLocale()
  return <>
    <button onClick={() => setLocale('en')}>English</button>
    <button onClick={() => setLocale('ko')}>한국어</button>
    <FilterPanel
      filter={DEFAULT_FILTER}
      onChange={() => {}}
      counts={{ user: 1, machine: 1, agent: 1, room: 1, project: 0 }}
      knownEngines={['codex']}
      knownStates={['running']}
    />
  </>
}

it('switches topology filter labels and search name with the selected locale', () => {
  render(<LocaleProvider><Harness /></LocaleProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'English' }))
  expect(screen.getByRole('complementary', { name: 'Topology filters' })).toBeInTheDocument()
  expect(screen.getByRole('searchbox', { name: 'Search' })).toHaveAttribute('placeholder', 'Filter by name…')

  fireEvent.click(screen.getByRole('button', { name: '한국어' }))
  expect(screen.getByRole('complementary', { name: '토폴로지 필터' })).toBeInTheDocument()
  expect(screen.getByRole('searchbox', { name: '검색' })).toHaveAttribute('placeholder', '이름으로 필터…')
})
