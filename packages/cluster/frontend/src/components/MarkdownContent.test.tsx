// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MarkdownContent from './MarkdownContent'

afterEach(() => cleanup())

const ESC = '\x1b'

describe('MarkdownContent — ANSI rendering in code blocks (#290 Phase A)', () => {
  it('renders plain code blocks without injecting any ANSI spans', () => {
    const content = '```\nhello world\n```'
    const { container } = render(<MarkdownContent content={content} />)
    expect(screen.getByText('hello world')).toBeInTheDocument()
    expect(container.querySelectorAll('span[data-ansi-segment]')).toHaveLength(0)
  })

  it('wraps ANSI-colored content in styled spans', () => {
    // Standard 8-color red foreground (SGR 31). Anser maps it to "187, 0, 0".
    const content = '```\n' + ESC + '[31mred-text' + ESC + '[0m\n```'
    const { container } = render(<MarkdownContent content={content} />)
    const segments = container.querySelectorAll('span[data-ansi-segment]')
    const reds = Array.from(segments).filter(s => s.textContent === 'red-text')
    expect(reds).toHaveLength(1)
    expect((reds[0] as HTMLElement).style.color).toBe('rgb(187, 0, 0)')
  })

  it('handles 256-color foreground sequences (codex output style)', () => {
    // SGR 38;5;46 → palette index 46 = bright green-ish (0,215,0).
    const content = '```\n' + ESC + '[38;5;46m#' + ESC + '[0m\n```'
    const { container } = render(<MarkdownContent content={content} />)
    const segments = container.querySelectorAll('span[data-ansi-segment]')
    const colored = Array.from(segments).find(s => s.textContent === '#')
    expect(colored).toBeDefined()
    // Don't pin the exact palette RGB — just assert a real rgb(...) was set.
    expect((colored as HTMLElement).style.color).toMatch(/^rgb\(/)
  })

  it('applies bold decoration as fontWeight', () => {
    const content = '```\n' + ESC + '[1mbold' + ESC + '[0m\n```'
    const { container } = render(<MarkdownContent content={content} />)
    const segments = container.querySelectorAll('span[data-ansi-segment]')
    const bolds = Array.from(segments).filter(s => s.textContent === 'bold')
    expect(bolds).toHaveLength(1)
    expect((bolds[0] as HTMLElement).style.fontWeight).toBe('bold')
  })

  it('preserves plain segments alongside ANSI segments in mixed content', () => {
    const content = '```\nplain ' + ESC + '[31mred' + ESC + '[0m\n```'
    const { container } = render(<MarkdownContent content={content} />)
    const code = container.querySelector('code')
    expect(code).not.toBeNull()
    expect(code!.textContent).toContain('plain')
    expect(code!.textContent).toContain('red')
    const segments = container.querySelectorAll('span[data-ansi-segment]')
    const reds = Array.from(segments).filter(s => s.textContent === 'red')
    expect(reds).toHaveLength(1)
  })

  it('does not interpret ANSI sequences in inline code (only fenced blocks)', () => {
    // Inline `code` is single-line and rarely contains ANSI; we deliberately
    // only colorize multi-line code blocks. This guards against accidental
    // ANSI rendering bleeding into prose-adjacent inline snippets.
    const content = 'see this `' + ESC + '[31mred' + ESC + '[0m` snippet'
    const { container } = render(<MarkdownContent content={content} />)
    expect(container.querySelectorAll('span[data-ansi-segment]')).toHaveLength(0)
  })

  it('strips ANSI escapes from plain (non-code) markdown body', () => {
    // Sanity: ANSI sequences in regular paragraph text get escaped/ignored
    // by the markdown renderer; we don't try to colorize prose.
    const content = 'a paragraph with ' + ESC + '[31mred' + ESC + '[0m inside'
    const { container } = render(<MarkdownContent content={content} />)
    expect(container.querySelectorAll('span[data-ansi-segment]')).toHaveLength(0)
  })
})

describe('MarkdownContent — regression: plain markdown still renders', () => {
  it('renders headings and paragraphs unchanged', () => {
    const content = '# Title\n\nbody text'
    render(<MarkdownContent content={content} />)
    expect(
      screen.getByRole('heading', { name: 'Title', level: 1 }),
    ).toBeInTheDocument()
    expect(screen.getByText('body text')).toBeInTheDocument()
  })

  it('still renders mention pills for <@user:...> tokens', () => {
    const content = 'hello <@user:abc123>'
    render(
      <MarkdownContent
        content={content}
        resolveUser={id => (id === 'abc123' ? 'Alice' : undefined)}
      />,
    )
    expect(screen.getByText('@Alice')).toBeInTheDocument()
  })
})

describe('MarkdownContent — file reference rendering', () => {
  const fileReferenceCandidates = [
    {
      id: 'file-1',
      name: 'spec.md',
      storage_name: 'spec.md',
    },
    {
      id: 'file-2',
      name: 'Original Name.md',
      storage_name: 'sanitized.md',
    },
  ]

  it('renders valid $filename tokens as inline file reference pills', () => {
    const { container } = render(
      <MarkdownContent
        content="please read $spec.md"
        fileReferenceCandidates={fileReferenceCandidates}
      />,
    )

    const ref = container.querySelector('[data-file-reference="file-1"]')
    expect(ref).not.toBeNull()
    expect(ref).toHaveTextContent('$spec.md')
  })

  it('resolves by storage_name and preserves trailing punctuation', () => {
    const { container } = render(
      <MarkdownContent
        content="compare $sanitized.md."
        fileReferenceCandidates={fileReferenceCandidates}
      />,
    )

    const ref = container.querySelector('[data-file-reference="file-2"]')
    expect(ref).not.toBeNull()
    expect(ref).toHaveTextContent('$sanitized.md')
    expect(container.textContent).toContain('$sanitized.md.')
  })

  it('leaves unknown and shell-like $ tokens as plain text', () => {
    const { container } = render(
      <MarkdownContent
        content="echo $HOME, pay $10, run $(date), see abc$spec.md and $missing.txt"
        fileReferenceCandidates={fileReferenceCandidates}
      />,
    )

    expect(container.querySelectorAll('[data-file-reference]')).toHaveLength(0)
    expect(container).toHaveTextContent('$HOME')
    expect(container).toHaveTextContent('$missing.txt')
  })

  it('does not render file references inside inline or fenced code', () => {
    const { container } = render(
      <MarkdownContent
        content={'inline `$spec.md`\n\n```\n$spec.md\n```'}
        fileReferenceCandidates={fileReferenceCandidates}
      />,
    )

    expect(container.querySelectorAll('[data-file-reference]')).toHaveLength(0)
    expect(container.querySelectorAll('code')).toHaveLength(2)
  })
})
