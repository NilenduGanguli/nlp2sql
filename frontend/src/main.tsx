import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  ModuleRegistry,
  ClientSideRowModelModule,
  CommunityFeaturesModule,
  CsvExportModule,
} from 'ag-grid-community'
import App from './App'
import './styles.css'
import './index.css'

// Register AG Grid Community modules (v32)
ModuleRegistry.registerModules([ClientSideRowModelModule, CommunityFeaturesModule, CsvExportModule])

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
