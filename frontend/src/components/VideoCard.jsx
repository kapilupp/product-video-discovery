function scoreColor(score) {
  if (score >= 80) return '#6fcf97'
  if (score >= 55) return '#e6b95c'
  return '#ff7a7a'
}

export default function VideoCard({ video }) {
  return (
    <div className="video-card">
      <a href={video.video_url} target="_blank" rel="noreferrer" className="thumbnail-wrap">
        {video.thumbnail_url ? (
          <img src={video.thumbnail_url} alt="" className="thumbnail" />
        ) : (
          <div className="thumbnail placeholder">
            <PlaceholderIcon />
          </div>
        )}
        <div className="match-ring" style={{ '--score-color': scoreColor(video.match_score) }}>
          {video.match_score}
        </div>
        <div className="play-badge">
          <PlayIcon />
        </div>
      </a>
      <div className="video-body">
        <div className="video-meta">
          <span className={`platform-badge ${video.platform}`}>{video.platform}</span>
          {video.seen_before && <span className="seen-badge">Seen before</span>}
        </div>
        {video.caption && <p className="caption">{video.caption}</p>}
        {video.match_reason && <p className="match-reason">{video.match_reason}</p>}
      </div>
    </div>
  )
}

function PlaceholderIcon() {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="9" cy="10" r="1.5" fill="currentColor" />
      <path d="M4 17l5-5 3 3 3-4 5 6" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg width="42" height="42" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="11" fill="rgba(255,255,255,0.16)" stroke="white" strokeWidth="1.3" />
      <path d="M10 8.5l6 3.5-6 3.5v-7Z" fill="white" />
    </svg>
  )
}
