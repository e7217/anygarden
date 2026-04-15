import { describe, it, expect } from 'vitest'
import {
  resolveRoomMentionsInText,
  extractMentionsMetadata,
  parseMentionTokens,
} from './mentions'

describe('resolveRoomMentionsInText', () => {
  const rooms = [
    { id: 'r1', display: '테스트룸1' },
    { id: 'r2', display: '테스트룸2' },
    { id: 'dup', display: '중복방' },
    { id: 'dup2', display: '중복방' },
    { id: 'mixed', display: 'Dev-Room_42' },
  ]

  it('1) single match at start of content is replaced with room token', () => {
    expect(resolveRoomMentionsInText('#테스트룸2 안녕?', rooms)).toBe('<#room:r2> 안녕?')
  })

  it('2) inline match (preceded by whitespace) is replaced', () => {
    expect(
      resolveRoomMentionsInText('hey #테스트룸1 check this', rooms),
    ).toBe('hey <#room:r1> check this')
  })

  it('3) multiple matches in same content are each replaced independently', () => {
    expect(
      resolveRoomMentionsInText('#테스트룸1 and #테스트룸2 both', rooms),
    ).toBe('<#room:r1> and <#room:r2> both')
  })

  it('4) duplicate display names are left as plaintext (safe fallback)', () => {
    expect(resolveRoomMentionsInText('#중복방 hi', rooms)).toBe('#중복방 hi')
  })

  it('5) no matching room is left as plaintext (fallback UX preserved)', () => {
    expect(resolveRoomMentionsInText('#없는방 hi', rooms)).toBe('#없는방 hi')
  })

  it('6) existing <#room:xxx> tokens are NOT re-tokenized', () => {
    const input = 'see <#room:r1> for context #테스트룸2'
    // existing token remains; plaintext gets converted
    expect(resolveRoomMentionsInText(input, rooms)).toBe(
      'see <#room:r1> for context <#room:r2>',
    )
  })

  it('7) supports Korean/English/digit mixed room names', () => {
    expect(resolveRoomMentionsInText('#Dev-Room_42 ping', rooms)).toBe(
      '<#room:mixed> ping',
    )
  })

  it('8) trailing punctuation is preserved outside the token', () => {
    expect(resolveRoomMentionsInText('go to #테스트룸2.', rooms)).toBe(
      'go to <#room:r2>.',
    )
    expect(resolveRoomMentionsInText('#테스트룸1, 어때?', rooms)).toBe(
      '<#room:r1>, 어때?',
    )
  })

  it('integration: extractMentionsMetadata picks up resolved room tokens', () => {
    const resolved = resolveRoomMentionsInText('#테스트룸2 hi', rooms)
    expect(extractMentionsMetadata(resolved)).toEqual([{ type: 'room', id: 'r2' }])
    // parseMentionTokens should see the same mention
    expect(parseMentionTokens(resolved)).toEqual([
      { type: 'room', id: 'r2' },
    ])
  })

  it('returns original content when rooms list is empty', () => {
    expect(resolveRoomMentionsInText('#테스트룸2 hi', [])).toBe('#테스트룸2 hi')
  })

  it('does not treat mid-word # as a mention (e.g. abc#def)', () => {
    expect(resolveRoomMentionsInText('abc#테스트룸2', rooms)).toBe('abc#테스트룸2')
  })
})
