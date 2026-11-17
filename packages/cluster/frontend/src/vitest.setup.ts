// Tests render synchronously, so both languages are registered up front instead
// of being loaded on demand as in the app (see i18n/messages.ts).
import { registerMessages } from './i18n/messages'
import { en } from './i18n/locales/en'
import { ko } from './i18n/locales/ko'

registerMessages('en', en)
registerMessages('ko', ko)
