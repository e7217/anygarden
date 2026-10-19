// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

import EngineSetupHint from './EngineSetupHint'

const json = (body: unknown, ok = true) => Promise.resolve({ ok, json: async () => body })

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { cleanup(); vi.useRealTimers(); mocks.apiFetch.mockReset() })

it('[NODE-16] shows how to install an engine on the machine', () => {
  render(<EngineSetupHint machineId="m1" machineName="Studio" onDetected={vi.fn()} />)
  expect(screen.getByText(/No engine on Studio yet/)).toBeInTheDocument()
  expect(screen.getByText('npm i -g @openai/codex && codex login')).toBeInTheDocument()
  expect(screen.getByText('npm i -g @earendil-works/pi-coding-agent')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy the Codex command' })).toBeInTheDocument()
})

it('[NODE-16] asks the machine to detect again and reports a found engine', async () => {
  let engines: unknown[] = []
  mocks.apiFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/v1/machines/m1/engines/rescan' && init?.method === 'POST') return json({ status: 'rescanning' })
    if (url === '/api/v1/machines/m1/engines') return json(engines)
    return json({}, false)
  })
  const onDetected = vi.fn()
  render(<EngineSetupHint machineId="m1" machineName="Studio" onDetected={onDetected} />)

  fireEvent.click(screen.getByRole('button', { name: 'Detect again' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(onDetected).not.toHaveBeenCalled()
  engines = [{ engine: 'codex-cli' }]
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  await waitFor(() => expect(onDetected).toHaveBeenCalledTimes(1))
})

it('says so when nothing turns up in time', async () => {
  mocks.apiFetch.mockImplementation((url: string) => json(url.endsWith('/rescan') ? {} : []))
  render(<EngineSetupHint machineId="m1" onDetected={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Detect again' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
  expect(await screen.findByText(/No engine found yet/)).toBeInTheDocument()
})

it('explains a machine that could not be asked', async () => {
  mocks.apiFetch.mockImplementation(() => json({}, false))
  render(<EngineSetupHint machineId="m1" onDetected={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Detect again' }))
  expect(await screen.findByText(/Could not ask the machine/)).toBeInTheDocument()
})
