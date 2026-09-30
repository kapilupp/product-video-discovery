import { useMemo, useState } from 'react'
import Filters from './Filters.jsx'
import VideoCard from './VideoCard.jsx'

const MIN_PER_SOURCE = 20

export default function ResultsGrid({ search, showSeen }) {
  const [tab, setTab] = useState('instagram')
  const [sortBy, setSortBy] = useState('match_score')
  const [platform, setPlatform] = useState('all')

  const videos = search?.videos || []

  const filtered = useMemo(() => {
    let list = videos.filter((v) => (showSeen ? true : !v.seen_before))
    if (tab !== 'all') list = list.filter((v) => v.platform === tab)
    if (platform !== 'all') list = list.filter((v) => v.platform === platform)
    if (sortBy === 'match_score') {
      list = [...list].sort((a, b) => b.match_score - a.match_score)
    } else if (sortBy === 'newest') {
      list = [...list].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    }
    return list
  }, [videos, tab, platform, sortBy, showSeen])

  const counts = {
    instagram: videos.filter((v) => v.platform === 'instagram').length,
    meta: videos.filter((v) => v.platform === 'meta').length,
    tiktok: videos.filter((v) => v.platform === 'tiktok').length,
  }

  return (
    <div className="results-grid">
      <div className="tabs">
        {['instagram', 'meta', 'tiktok'].map((p) => {
          const met = p === 'tiktok' ? null : counts[p] >= MIN_PER_SOURCE
          return (
            <button key={p} className={tab === p ? 'active' : ''} onClick={() => setTab(p)}>
              {p}
              <span className={`tab-count ${met === null ? '' : met ? 'met' : 'short'}`}>
                {counts[p]}/{p === 'tiktok' ? '—' : MIN_PER_SOURCE}
              </span>
            </button>
          )
        })}
      </div>

      <Filters sortBy={sortBy} onSortChange={setSortBy} platform={platform} onPlatformChange={setPlatform} />

      {filtered.length === 0 ? (
        <div className="empty-state">
          <EmptyIcon />
          <div>
            <strong>No videos yet for this tab.</strong>
            <br />
            If the search just finished, this source may have returned fewer than the minimum —
            check the progress log above for a shortfall warning.
          </div>
        </div>
      ) : (
        <div className="video-grid">
          {filtered.map((v) => (
            <VideoCard key={v.id} video={v} />
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="3" y="6" width="14" height="12" rx="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M17 10.5l4-2.2v7.4l-4-2.2" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}
