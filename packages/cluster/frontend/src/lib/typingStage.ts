export type AgentStage = 'preparing' | 'using_tool' | 'writing'

export function isAgentStage(value: unknown): value is AgentStage {
  return value === 'preparing' || value === 'using_tool' || value === 'writing'
}

const stageText: Record<AgentStage, string> = {
  preparing: '응답 준비 중…',
  using_tool: '도구 실행 중…',
  writing: '응답 작성 중…',
}

export function typingParticipantLabel(name: string, stage?: AgentStage): string {
  return stage ? `${name} · ${stageText[stage]}` : name
}
