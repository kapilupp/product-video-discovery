import { useEffect, useState } from 'react'
import { createSearch, getHistory, getSearch, streamSearchProgress } from './api/client.js'
import SearchBar from './components/SearchBar.jsx'
import ProductPanel from './components/ProductPanel.jsx'
import ProgressTracker from './components/ProgressTracker.jsx'
import ResultsGrid from './components/ResultsGrid.jsx'
import SearchHistory from './components/SearchHistory.jsx'

export default function App() {
  const [search, setSearch] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [showSeen, setShowSeen] = useState(false)

  useEffect(() => {
    refreshHistory()
  }, [])

  async function refreshHistory() {
    try {
      setHistory(await getHistory())
    } catch {
      // history is a nice-to-have; ignore failures here
    }
  }

  async function handleSearch(params) {
    setLoading(true)
    setError(null)
    setEvents([])
    try {
      const created = await createSearch(params)
      setSearch(created)

      const stop = streamSearchProgress(created.id, async (event) => {
        setEvents((prev) => [...prev, event])
        if (event.stage === 'done' || event.stage === 'failed') {
          const finalResult = await getSearch(created.id)
          setSearch(finalResult)
          setLoading(false)
          refreshHistory()
          stop()
        }
      })
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
      setLoading(false)
    }
  }

  async function handleSelectHistory(id) {
    setLoading(true)
    setError(null)
    try {
      setSearch(await getSearch(id))
      setEvents([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div className="brand-text">
            <h1>Product Video Discovery</h1>
            <span>AI-matched Reels &amp; Ad Library videos</span>
          </div>
        </div>
        <div className="header-meta">
          <span className="status-dot" />
          Live
        </div>
      </header>

      <div className="app-container">
        <div className="hero">
          <span className="hero-eyebrow">✦ Vision-matched sourcing</span>
          <h1>Find every video that shows your exact product</h1>
          <p>
            Type a product name, paste a product page, or upload a photo — we'll surface matching
            Instagram Reels and Meta Ad Library videos, ranked by visual match.
          </p>
        </div>

        <SearchBar onSearch={handleSearch} loading={loading} />

        {error && (
          <div className="error-banner">
            <ErrorIcon /> {error}
          </div>
        )}

        <ProgressTracker events={events} />

        {search?.status === 'failed' && (
          <div className="error-banner">
            <ErrorIcon /> {search.error_message || 'Search failed.'}
          </div>
        )}

        <ProductPanel search={search} />

        {search && (
          <label className="show-seen-toggle">
            <input type="checkbox" checked={showSeen} onChange={(e) => setShowSeen(e.target.checked)} />
            Show previously seen videos
          </label>
        )}

        <ResultsGrid search={search} showSeen={showSeen} />

        <SearchHistory history={history} onSelect={handleSelectHistory} />
      </div>
    </div>
  )
}

function ErrorIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 8v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="16" r="1" fill="currentColor" />
    </svg>
  )
}
