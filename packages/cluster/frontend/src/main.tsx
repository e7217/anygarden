import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { LocaleProvider } from './i18n/LocaleProvider'
import { ThemeProvider } from './theme/ThemeProvider'
import { FeedbackProvider } from './components/feedback/FeedbackProvider'
import './index.css'

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
