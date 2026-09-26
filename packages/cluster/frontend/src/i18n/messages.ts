import { coreEn, coreKo } from './catalogs/core'
import { chatEn, chatKo } from './catalogs/chat'
import { adminEn, adminKo } from './catalogs/admin'
import { guestEn, guestKo } from './catalogs/guest'
import { federationEn, federationKo } from './catalogs/federation'
import { roomsEn, roomsKo } from './catalogs/rooms'
import { workflowsEn, workflowsKo } from './catalogs/workflows'

export const en = { ...coreEn, ...chatEn, ...adminEn, ...guestEn, ...federationEn, ...roomsEn, ...workflowsEn } as const
export type MessageKey = keyof typeof en
export const ko: Record<MessageKey, string> = { ...coreKo, ...chatKo, ...adminKo, ...guestKo, ...federationKo, ...roomsKo, ...workflowsKo }
