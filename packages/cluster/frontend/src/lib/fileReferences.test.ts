import { describe, it, expect } from 'vitest'
import {
  buildFileReferenceCandidates,
  buildSharedFileReference,
  dedupeSharedFileReferences,
  extractSharedFileReferencesFromMetadata,
  resolveFileReferenceToken,
  resolveFileReferencesInText,
} from './fileReferences'
import type { RoomSharedFile } from './roomFiles'

const file = (
  id: string,
  filename: string,
  storageName = filename,
): RoomSharedFile => ({
  id,
  room_id: 'room-1',
  filename,
  storage_name: storageName,
  sha256: `sha-${id}`,
  size_bytes: 12,
  mime: 'text/plain',
  uploaded_by: null,
  created_at: '2026-05-10T00:00:00Z',
})

describe('buildSharedFileReference', () => {
  it('uses stable shared-file metadata from the room file row', () => {
    expect(buildSharedFileReference(file('f1', 'Spec.md', 'spec.md'), 'inline')).toEqual({
      type: 'shared_file',
      id: 'f1',
      name: 'Spec.md',
      storage_name: 'spec.md',
      sha256: 'sha-f1',
      origin: 'inline',
    })
  })
})

describe('resolveFileReferencesInText', () => {
  const files = [
    file('f1', 'spec.md'),
    file('f2', 'data.json'),
    file('f3', 'Original Name.md', 'sanitized.md'),
    file('d1', 'dup.md'),
    file('d2', 'dup.md'),
  ]

  it('resolves a unique directly typed $filename reference', () => {
    const refs = resolveFileReferencesInText('please read $spec.md', files)
    expect(refs).toEqual([
      {
        type: 'shared_file',
        id: 'f1',
        name: 'spec.md',
        storage_name: 'spec.md',
        sha256: 'sha-f1',
        origin: 'inline',
      },
    ])
  })

  it('also resolves by storage_name', () => {
    const refs = resolveFileReferencesInText('see $sanitized.md', files)
    expect(refs.map(r => r.id)).toEqual(['f3'])
  })

  it('preserves shell variables, prices, command substitution, and mid-word dollars as plain text', () => {
    const refs = resolveFileReferencesInText(
      'echo $HOME and pay $10 and run $(date) and abc$spec.md',
      files,
    )
    expect(refs).toEqual([])
  })

  it('does not resolve duplicate filenames or unknown files', () => {
    expect(resolveFileReferencesInText('$dup.md $missing.md', files)).toEqual([])
  })

  it('ignores trailing punctuation when matching the file token', () => {
    const refs = resolveFileReferencesInText('compare $data.json.', files)
    expect(refs.map(r => r.id)).toEqual(['f2'])
  })
})

describe('dedupeSharedFileReferences', () => {
  it('dedupes by id and prefers attachment-origin references', () => {
    const inline = buildSharedFileReference(file('f1', 'spec.md'), 'inline')
    const attachment = buildSharedFileReference(file('f1', 'spec.md'), 'attachment')
    const other = buildSharedFileReference(file('f2', 'data.json'), 'inline')

    expect(dedupeSharedFileReferences([inline, other, attachment])).toEqual([
      attachment,
      other,
    ])
  })
})

describe('extractSharedFileReferencesFromMetadata', () => {
  it('keeps canonical shared-file metadata and ignores malformed entries', () => {
    const refs = extractSharedFileReferencesFromMetadata({
      references: [
        {
          type: 'shared_file',
          id: 'f1',
          name: 'spec.md',
          storage_name: 'spec.md',
          origin: 'inline',
        },
        { type: 'other', id: 'x', name: 'ignored.md' },
        { type: 'shared_file', id: 123, name: 'bad.md' },
      ],
    })

    expect(refs).toEqual([
      {
        type: 'shared_file',
        id: 'f1',
        name: 'spec.md',
        storage_name: 'spec.md',
        origin: 'inline',
      },
    ])
  })
})

describe('buildFileReferenceCandidates', () => {
  it('prefers metadata references and appends current room files', () => {
    const candidates = buildFileReferenceCandidates(
      {
        references: [
          {
            type: 'shared_file',
            id: 'f1',
            name: 'historical.md',
            storage_name: 'historical.md',
          },
        ],
      },
      [file('f1', 'spec.md'), file('f2', 'data.json')],
    )

    expect(candidates.map(c => [c.id, c.name])).toEqual([
      ['f1', 'historical.md'],
      ['f2', 'data.json'],
    ])
  })
})

describe('resolveFileReferenceToken', () => {
  const candidates = buildFileReferenceCandidates(null, [
    file('f1', 'spec.md'),
    file('f2', 'Original Name.md', 'sanitized.md'),
    file('d1', 'dup.md'),
    file('d2', 'dup.md'),
  ])

  it('resolves exact unique filename or storage_name tokens', () => {
    expect(resolveFileReferenceToken('spec.md', candidates)?.candidate.id).toBe('f1')
    expect(resolveFileReferenceToken('sanitized.md', candidates)?.candidate.id).toBe('f2')
  })

  it('preserves trailing punctuation outside the token', () => {
    const resolved = resolveFileReferenceToken('spec.md.', candidates)
    expect(resolved?.token).toBe('spec.md')
    expect(resolved?.suffix).toBe('.')
  })

  it('rejects ambiguous, unknown, shell-like, and price tokens', () => {
    expect(resolveFileReferenceToken('dup.md', candidates)).toBeNull()
    expect(resolveFileReferenceToken('missing.md', candidates)).toBeNull()
    expect(resolveFileReferenceToken('HOME', candidates)).toBeNull()
    expect(resolveFileReferenceToken('10', candidates)).toBeNull()
  })
})
