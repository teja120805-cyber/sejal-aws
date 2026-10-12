import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import { AppProvider } from './context/AppContext.tsx'
import { ThemeProvider } from './context/ThemeContext.tsx'
import { BootError, LoadingScreen, LoginScreen } from './pages/AuthScreens.tsx'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <AppProvider
          loadingScreen={<LoadingScreen />}
          loginScreen={<LoginScreen />}
          errorScreen={(message, retry) => <BootError message={message} retry={retry} />}
        >
          <App />
        </AppProvider>
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>,
)
