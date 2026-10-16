// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageAttachments from './MessageAttachments'
import { LocaleProvider } from '@/i18n/LocaleProvider'

const fetchArtifactBlobUrl = vi.fn()
const startArtifactDownload = vi.fn()

vi.mock('@/lib/roomArtifacts', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/roomArtifacts')>()),
  fetchArtifactBlobUrl: (...args: unknown[]) => fetchArtifactBlobUrl(...args),
  startArtifactDownload: (...args: unknown[]) => startArtifactDownload(...args),
}))

beforeEach(() => {
  localStorage.setItem('anygarden_locale', 'en')
  fetchArtifactBlobUrl.mockResolvedValue({ url: 'blob:x', filename: 'f', revoke: vi.fn() })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const image = {
  type: 'artifact', id: 'a1', room_id: 'r1', name: 'chart.png',
  mime: 'image/png', size_bytes: 2048, sha256: 's',
}
const pdf = {
  type: 'artifact', id: 'a2', room_id: 'r1', name: 'report.pdf',
  mime: 'application/pdf', size_bytes: 3 * 1024 * 1024, sha256: 't',
}

function renderWith(metadata: Record<string, unknown>) {
  return render(
    <LocaleProvider>
      <MessageAttachments metadata={metadata} />
    </LocaleProvider>,
  )
}

describe('MessageAttachments', () => {
  it('renders nothing without artifact references', () => {
    const { container } = renderWith({
      references: [{ type: 'shared_file', id: 'f1', name: 'spec.md' }],
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('shows an image inline and opens it larger', async () => {
    renderWith({ references: [image] })
    const thumb = await screen.findByRole('img', { name: 'chart.png' })
    expect(thumb).toHaveAttribute('src', 'blob:x')
    expect(fetchArtifactBlobUrl).toHaveBeenCalledWith('r1', 'a1')

    fireEvent.click(screen.getByRole('button', { name: 'Open chart.png' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download chart.png' })).toBeInTheDocument()
  })

  it('shows other files as a download card', async () => {
    renderWith({ references: [pdf] })
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('3.0 MB')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download report.pdf' }))
    await waitFor(() => expect(startArtifactDownload).toHaveBeenCalled())
    expect(fetchArtifactBlobUrl).toHaveBeenCalledWith('r1', 'a2')
  })

  it('marks an image whose file is gone as unavailable', async () => {
    fetchArtifactBlobUrl.mockRejectedValue(new Error('Download failed (HTTP 404)'))
    renderWith({ references: [image] })
    expect(await screen.findByText('File no longer available')).toBeInTheDocument()
  })

  it('lists attachments that could not be delivered', () => {
    renderWith({
      undelivered_attachments: [
        { name: 'huge.png', reason: 'too_large' },
        { name: 'x.bin', reason: 'unsupported_type' },
        { name: 'junk', reason: 'nonsense' },
      ],
    })
    expect(screen.getByText('Not attached · huge.png (file too large)')).toBeInTheDocument()
    expect(screen.getByText('Not attached · x.bin (file type not allowed)')).toBeInTheDocument()
    expect(screen.getByText('Not attached · junk (upload failed)')).toBeInTheDocument()
  })
})
