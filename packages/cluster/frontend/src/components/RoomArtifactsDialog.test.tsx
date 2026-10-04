// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomArtifactsDialog from './RoomArtifactsDialog'
import type { RoomArtifact } from '@/lib/roomArtifacts'

vi.mock('@/components/feedback/FeedbackProvider', () => ({ useFeedback: () => ({ confirm: vi.fn().mockResolvedValue(true) }) }))

const artifact: RoomArtifact = {
  id: 'artifact-1', room_id: 'career-room', produced_by_agent_id: 'career-writer',
  filename: 'resume.md', sha256: 'a'.repeat(64), size_bytes: 31,
  mime: 'text/markdown', created_at: '2026-09-30T09:00:00Z',
}
const props = { roomId: 'career-room', open: true, onOpenChange: vi.fn() }
const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  localStorage.setItem('anygarden_token', 'test-session')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/artifacts') ? json([artifact]) : new Response('# Verified resume')))
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn().mockReturnValue('blob:test-artifact')
    static revokeObjectURL = vi.fn()
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('downloads authenticated artifact bytes with the original filename and releases the blob URL', async () => {
  render(<RoomArtifactsDialog {...props} />)
  await screen.findByText('resume.md')
  vi.useFakeTimers()
  let downloaded: { href: string; filename: string } | undefined
  vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function (this: HTMLAnchorElement) {
    downloaded = { href: this.href, filename: this.download }
  })

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Download' })) })

  expect(fetch).toHaveBeenCalledWith('/api/v1/rooms/career-room/artifacts/artifact-1', {
    method: 'GET', headers: { Authorization: 'Bearer test-session' },
  })
  expect(downloaded).toEqual({ href: 'blob:test-artifact', filename: 'resume.md' })
  expect(screen.queryByRole('link', { name: 'Download' })).not.toBeInTheDocument()
  act(() => vi.runAllTimers())
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-artifact')
})

it('shows the artifact producer and content fingerprint for result review', async () => {
  render(<RoomArtifactsDialog {...props} />)
  expect(await screen.findByText(/career-writer/)).toBeInTheDocument()
  expect(screen.getByTitle(artifact.sha256)).toHaveTextContent('SHA-256')
})

it('reports download failure and allows retry without opening an unauthenticated link', async () => {
  vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith('/artifacts') ? json([artifact]) : new Response('', { status: 403 }))
  render(<RoomArtifactsDialog {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Download failed (HTTP 403)')
  expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled()
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
})

it('rejects a late list response from the previously selected project room', async () => {
  let resolveCareer!: (response: Response) => void
  vi.mocked(fetch).mockImplementation(async (url) => String(url).includes('career-room')
    ? new Promise<Response>(resolve => { resolveCareer = resolve })
    : json([{ ...artifact, room_id: 'garden-room', filename: 'release-plan.md' }]))
  const view = render(<RoomArtifactsDialog {...props} />)
  view.rerender(<RoomArtifactsDialog {...props} roomId="garden-room" />)
  await screen.findByText('release-plan.md')
  await act(async () => { resolveCareer(json([artifact])) })
  expect(screen.queryByText('resume.md')).not.toBeInTheDocument()
  expect(screen.getByText('release-plan.md')).toBeInTheDocument()
})

it('discards a download that finishes after navigation to another project room', async () => {
  let resolveDownload!: (response: Response) => void
  vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith('/artifacts')
    ? json(String(url).includes('career-room') ? [artifact] : [])
    : new Promise<Response>(resolve => { resolveDownload = resolve }))
  const view = render(<RoomArtifactsDialog {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
  await waitFor(() => expect(resolveDownload).toBeDefined())
  view.rerender(<RoomArtifactsDialog {...props} roomId="garden-room" />)
  await act(async () => { resolveDownload(new Response('# Old project resume')) })
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-artifact')
  expect(screen.queryByText('resume.md')).not.toBeInTheDocument()
})
