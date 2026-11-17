import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { LocaleProvider, readInitialLocale } from './i18n/LocaleProvider'
import { loadMessages } from './i18n/messages'
import { ThemeProvider } from './theme/ThemeProvider'
import { FeedbackProvider } from './components/feedback/FeedbackProvider'
import './index.css'

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ThemeProvider>
        <LocaleProvider>
          <FeedbackProvider>
            <App />
          </FeedbackProvider>
        </LocaleProvider>
      </ThemeProvider>
    </StrictMode>
  )
}

// Only the starting language's catalog is fetched, and before the first paint so
// no untranslated keys flash. If it cannot load, fall back to English, and
// render regardless so the app is never left blank.
const initialLocale = readInitialLocale()
loadMessages(initialLocale)
  .catch(() => (initialLocale === 'en' ? undefined : loadMessages('en')))
  .catch(() => undefined)
  .finally(render)
