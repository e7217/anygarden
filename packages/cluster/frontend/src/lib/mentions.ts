// frontend/src/lib/mentions.ts

export interface Mention {
  type: 'user' | 'room'
  id: string
  display?: string
}

/** content 문자열에서 <@user:id> / <#room:id> 토큰을 찾아 반환 */
export function parseMentionTokens(content: string): Mention[] {
  const re = /<@user:([^>]+)>|<#room:([^>]+)>/g
  const mentions: Mention[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    if (m[1]) mentions.push({ type: 'user', id: m[1] })
    if (m[2]) mentions.push({ type: 'room', id: m[2] })
  }
  return mentions
}

/**
 * 자동완성에서 선택 시 content에 삽입할 토큰 생성.
 * 예: insertMentionToken('user', 'abc123') => '<@user:abc123>'
 */
export function insertMentionToken(type: 'user' | 'room', id: string): string {
  return type === 'user' ? `<@user:${id}>` : `<#room:${id}>`
}

/**
 * 전송 전에 content에서 멘션 토큰을 추출하여 metadata.mentions 배열 생성.
 */
export function extractMentionsMetadata(content: string): Pick<Mention, 'type' | 'id'>[] {
  return parseMentionTokens(content).map(m => ({ type: m.type, id: m.id }))
}
