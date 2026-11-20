// Catalog imports carrying a `?locale=` query (src/i18n/locales). Vite loads
// the same file under a separate module id, so each locale's chunk keeps only
// the objects it uses.
declare module '*?locale=en' {
  export const agentSetupEn: typeof import('../catalogs/agentSetup').agentSetupEn
  export const coreEn: typeof import('../catalogs/core').coreEn
  export const chatEn: typeof import('../catalogs/chat').chatEn
  export const adminEn: typeof import('../catalogs/admin').adminEn
  export const guestEn: typeof import('../catalogs/guest').guestEn
  export const federationEn: typeof import('../catalogs/federation').federationEn
  export const roomsEn: typeof import('../catalogs/rooms').roomsEn
  export const workflowsEn: typeof import('../catalogs/workflows').workflowsEn
  export const inboxEn: typeof import('../catalogs/inbox').inboxEn
  export const projectExecutionsEn: typeof import('../catalogs/projectExecutions').projectExecutionsEn
  export const taskRecoveryEn: typeof import('../catalogs/taskRecovery').taskRecoveryEn
  export const budgetsEn: typeof import('../catalogs/budgets').budgetsEn
  export const campaignsEn: typeof import('../catalogs/campaigns').campaignsEn
  export const apiKeysEn: typeof import('../catalogs/apiKeys').apiKeysEn
  export const usersEn: typeof import('../catalogs/users').usersEn
  export const agentLinksEn: typeof import('../catalogs/agentLinks').agentLinksEn
  export const peerChainsEn: typeof import('../catalogs/peerChains').peerChainsEn
}
declare module '*?locale=ko' {
  export const agentSetupKo: typeof import('../catalogs/agentSetup').agentSetupKo
  export const coreKo: typeof import('../catalogs/core').coreKo
  export const chatKo: typeof import('../catalogs/chat').chatKo
  export const adminKo: typeof import('../catalogs/admin').adminKo
  export const guestKo: typeof import('../catalogs/guest').guestKo
  export const federationKo: typeof import('../catalogs/federation').federationKo
  export const roomsKo: typeof import('../catalogs/rooms').roomsKo
  export const workflowsKo: typeof import('../catalogs/workflows').workflowsKo
  export const inboxKo: typeof import('../catalogs/inbox').inboxKo
  export const projectExecutionsKo: typeof import('../catalogs/projectExecutions').projectExecutionsKo
  export const taskRecoveryKo: typeof import('../catalogs/taskRecovery').taskRecoveryKo
  export const budgetsKo: typeof import('../catalogs/budgets').budgetsKo
  export const campaignsKo: typeof import('../catalogs/campaigns').campaignsKo
  export const apiKeysKo: typeof import('../catalogs/apiKeys').apiKeysKo
  export const usersKo: typeof import('../catalogs/users').usersKo
  export const agentLinksKo: typeof import('../catalogs/agentLinks').agentLinksKo
  export const peerChainsKo: typeof import('../catalogs/peerChains').peerChainsKo
}
