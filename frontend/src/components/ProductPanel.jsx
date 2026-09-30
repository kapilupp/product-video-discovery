export default function ProductPanel({ search }) {
  if (!search) return null

  const attrs = search.product_attributes

  return (
    <div className="product-panel">
      {search.product_image_url && (
        <img src={search.product_image_url} alt={search.product_title || 'Product'} className="product-image" />
      )}
      <div className="product-info">
        <h2>{search.product_title || search.query_text}</h2>
        {attrs && (
          <ul className="attributes">
            {attrs.product_type && <li><strong>Type:</strong> {attrs.product_type}</li>}
            {attrs.colors && <li><strong>Colors:</strong> {attrs.colors.join(', ')}</li>}
            {attrs.pattern && <li><strong>Pattern:</strong> {attrs.pattern}</li>}
            {attrs.material && <li><strong>Material:</strong> {attrs.material}</li>}
          </ul>
        )}
      </div>
    </div>
  )
}
