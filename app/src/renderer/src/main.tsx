import React from 'react'
import ReactDOM from 'react-dom/client'
import './global.css'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary label="Ohmen">
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
