// Every Korean catalog, loaded on demand by loadMessages() (see ../messages.ts).
// The `?locale=ko` query makes each catalog a module of its own, so the English
// objects that share its source file tree-shake out of this chunk.
import type { MessageKey } from '../messages'
import { agentSetupKo } from '../catalogs/agentSetup?locale=ko'
import { coreKo } from '../catalogs/core?locale=ko'
import { chatKo } from '../catalogs/chat?locale=ko'
import { adminKo } from '../catalogs/admin?locale=ko'
import { guestKo } from '../catalogs/guest?locale=ko'
import { federationKo } from '../catalogs/federation?locale=ko'
import { roomsKo } from '../catalogs/rooms?locale=ko'
import { workflowsKo } from '../catalogs/workflows?locale=ko'
import { inboxKo } from '../catalogs/inbox?locale=ko'
import { projectExecutionsKo } from '../catalogs/projectExecutions?locale=ko'
import { taskRecoveryKo } from '../catalogs/taskRecovery?locale=ko'
import { budgetsKo } from '../catalogs/budgets?locale=ko'
import { campaignsKo } from '../catalogs/campaigns?locale=ko'
import { apiKeysKo } from '../catalogs/apiKeys?locale=ko'
import { agentLinksKo } from '../catalogs/agentLinks?locale=ko'
import { peerChainsKo } from '../catalogs/peerChains?locale=ko'

export const ko: Record<MessageKey, string> = { ...agentSetupKo, ...coreKo, ...chatKo, ...adminKo, ...guestKo, ...federationKo, ...roomsKo, ...workflowsKo, ...inboxKo, ...projectExecutionsKo, ...taskRecoveryKo, ...budgetsKo, ...campaignsKo, ...apiKeysKo, ...agentLinksKo, ...peerChainsKo }
