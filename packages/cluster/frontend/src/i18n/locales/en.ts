// Every English catalog, loaded on demand by loadMessages() (see ../messages.ts).
// The `?locale=en` query makes each catalog a module of its own, so the Korean
// objects that share its source file tree-shake out of this chunk.
import { agentSetupEn } from '../catalogs/agentSetup?locale=en'
import { coreEn } from '../catalogs/core?locale=en'
import { chatEn } from '../catalogs/chat?locale=en'
import { adminEn } from '../catalogs/admin?locale=en'
import { guestEn } from '../catalogs/guest?locale=en'
import { federationEn } from '../catalogs/federation?locale=en'
import { roomsEn } from '../catalogs/rooms?locale=en'
import { workflowsEn } from '../catalogs/workflows?locale=en'
import { inboxEn } from '../catalogs/inbox?locale=en'
import { projectExecutionsEn } from '../catalogs/projectExecutions?locale=en'
import { taskRecoveryEn } from '../catalogs/taskRecovery?locale=en'
import { budgetsEn } from '../catalogs/budgets?locale=en'
import { campaignsEn } from '../catalogs/campaigns?locale=en'
import { apiKeysEn } from '../catalogs/apiKeys?locale=en'
import { agentLinksEn } from '../catalogs/agentLinks?locale=en'
import { peerChainsEn } from '../catalogs/peerChains?locale=en'

export const en = { ...agentSetupEn, ...coreEn, ...chatEn, ...adminEn, ...guestEn, ...federationEn, ...roomsEn, ...workflowsEn, ...inboxEn, ...projectExecutionsEn, ...taskRecoveryEn, ...budgetsEn, ...campaignsEn, ...apiKeysEn, ...agentLinksEn, ...peerChainsEn } as const
