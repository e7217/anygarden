import { agentSetupEn, agentSetupKo } from './catalogs/agentSetup'
import { coreEn, coreKo } from './catalogs/core'
import { chatEn, chatKo } from './catalogs/chat'
import { adminEn, adminKo } from './catalogs/admin'
import { guestEn, guestKo } from './catalogs/guest'
import { federationEn, federationKo } from './catalogs/federation'
import { roomsEn, roomsKo } from './catalogs/rooms'
import { workflowsEn, workflowsKo } from './catalogs/workflows'
import { inboxEn, inboxKo } from './catalogs/inbox'
import { projectExecutionsEn, projectExecutionsKo } from './catalogs/projectExecutions'
import { taskRecoveryEn, taskRecoveryKo } from './catalogs/taskRecovery'

export const en = { ...agentSetupEn, ...coreEn, ...chatEn, ...adminEn, ...guestEn, ...federationEn, ...roomsEn, ...workflowsEn, ...inboxEn, ...projectExecutionsEn, ...taskRecoveryEn } as const
export type MessageKey = keyof typeof en
export const ko: Record<MessageKey, string> = { ...agentSetupKo, ...coreKo, ...chatKo, ...adminKo, ...guestKo, ...federationKo, ...roomsKo, ...workflowsKo, ...inboxKo, ...projectExecutionsKo, ...taskRecoveryKo }
