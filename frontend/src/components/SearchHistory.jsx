export default function SearchHistory({ history, onSelect }) {
  if (!history.length) return null

  return (
    <div className="search-history">
      <h3>Recent searches</h3>
      <ul>
        {history.map((item) => (
          <li key={item.id} onClick={() => onSelect(item.id)}>
            <span>{item.product_title || item.query_text || item.product_url}</span>
            <span className="counts">
              {item.instagram_count}+{item.meta_count} · {item.status}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
