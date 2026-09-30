export default function Filters({ sortBy, onSortChange, platform, onPlatformChange }) {
  return (
    <div className="filters">
      <label>
        Platform:
        <select value={platform} onChange={(e) => onPlatformChange(e.target.value)}>
          <option value="all">All</option>
          <option value="instagram">Instagram</option>
          <option value="meta">Meta Ad Library</option>
          <option value="tiktok">TikTok</option>
        </select>
      </label>
      <label>
        Sort by:
        <select value={sortBy} onChange={(e) => onSortChange(e.target.value)}>
          <option value="match_score">Match score</option>
          <option value="newest">Newest first</option>
        </select>
      </label>
    </div>
  )
}
