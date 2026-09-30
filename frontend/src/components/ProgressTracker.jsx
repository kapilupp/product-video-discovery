const STAGE_LABELS = {
  extracting_product: 'Reading product page…',
  analyzing_image: 'Analyzing product image…',
  fetching_sources: 'Fetching videos…',
  fetching_instagram: 'Searching Instagram Reels…',
  fetching_meta: 'Searching Meta Ad Library…',
  fetching_tiktok: 'Searching TikTok…',
  deduping: 'Removing duplicates…',
  scoring: 'Scoring matches…',
  done: 'Done',
  failed: 'Failed',
}

export default function ProgressTracker({ events }) {
  if (!events.length) return null
  const latest = events[events.length - 1]
  // "shortfall" events are emitted after "done", so the finished check must
  // look across all events, not just the most recent one.
  const finished = events.some((e) => e.stage === 'done' || e.stage === 'failed')
  const headline = finished
    ? STAGE_LABELS[events.find((e) => e.stage === 'done' || e.stage === 'failed').stage]
    : STAGE_LABELS[latest.stage] || latest.stage

  return (
    <div className="progress-tracker">
      <div className="progress-current">
        {!finished && <span className="progress-spinner" />}
        {headline}
      </div>
      <ul className="progress-log">
        {events
          .filter((e) => e.message)
          .map((e, i) => (
            <li key={i} className={e.stage.includes('error') || e.stage === 'shortfall' ? 'warn' : ''}>
              {e.message}
            </li>
          ))}
      </ul>
    </div>
  )
}
