// frontend/src/lib/mentions.ts

export interface Mention {
  type: 'user' | 'room'
  id: string
  display?: string
}

/** Minimal shape needed for room mention resolution (matches MentionOption). */
interface RoomMentionCandidate {
  id: string
  display: string
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

/**
 * 평문 `#RoomName` 을 `<#room:id>` 토큰으로 변환한다 (순수 함수).
 *
 * - 단어 경계 뒤의 `#` 만 매칭하여, 기존 `<#room:...>` 토큰 내부의 `#` 은 변환되지 않는다.
 * - `rooms` 에서 `display` 가 정확히 일치하는 항목이 **정확히 1건** 일 때만 치환한다.
 *   이름 중복(2건 이상) 또는 미매칭이면 원본 평문을 그대로 유지한다 (안전 fallback).
 * - 한 content 내 여러 `#RoomName` 도 각각 독립적으로 처리한다.
 *
 * 주의: 이 함수는 React 의존이 없는 순수 함수이며 side-effect 가 없다.
 * MessageInput 의 trackedMentions 치환 **이후**, extractMentionsMetadata **이전** 에 호출해야 한다.
 */
export function resolveRoomMentionsInText(
  content: string,
  rooms: readonly RoomMentionCandidate[],
): string {
  if (!content || rooms.length === 0) return content
  // 앞쪽이 문자열 시작 또는 공백일 때만 `#` 으로 시작하는 평문 룸 이름을 매칭한다.
  // 룸 이름 본문은 공백/꺾쇠괄호를 허용하지 않아 기존 토큰 `<#room:...>` 와 충돌하지 않는다.
  const re = /(^|\s)#([^\s<>#]+)/g
  return content.replace(re, (match, prefix: string, name: string) => {
    // 이름 끝의 단순 구두점은 매칭 범위에서 제외하여 치환 대상만 남긴다.
    const trailingPunct = name.match(/[.,!?;:)\]】。、]+$/)
    const cleanName = trailingPunct ? name.slice(0, -trailingPunct[0].length) : name
    const suffix = trailingPunct ? trailingPunct[0] : ''
    if (!cleanName) return match
    const matches = rooms.filter(r => r.display === cleanName)
    if (matches.length !== 1) return match
    return `${prefix}${insertMentionToken('room', matches[0].id)}${suffix}`
  })
}
