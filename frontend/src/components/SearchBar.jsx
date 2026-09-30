import { useRef, useState } from 'react'

const URL_PATTERN = /^https?:\/\//i
const MAX_IMAGE_BYTES = 5 * 1024 * 1024 // 5MB, keeps the base64 payload reasonable

export default function SearchBar({ onSearch, loading }) {
  const [value, setValue] = useState('')
  const [imageBase64, setImageBase64] = useState(null)
  const [imageName, setImageName] = useState('')
  const [imageError, setImageError] = useState('')
  const fileInputRef = useRef(null)

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setImageError('')

    if (file.size > MAX_IMAGE_BYTES) {
      setImageError('Image is too large (max 5MB).')
      e.target.value = ''
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      setImageBase64(reader.result) // data:image/...;base64,....
      setImageName(file.name)
    }
    reader.onerror = () => setImageError('Could not read that file.')
    reader.readAsDataURL(file)
  }

  function clearImage() {
    setImageBase64(null)
    setImageName('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!value.trim() && !imageBase64) return

    const base = URL_PATTERN.test(value.trim())
      ? { productUrl: value.trim() }
      : { queryText: value.trim() || undefined }

    onSearch({ ...base, imageBase64: imageBase64 || undefined })
  }

  return (
    <form className="search-bar" onSubmit={handleSubmit}>
      <div className="search-bar-row">
        <span className="search-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
            <path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </span>
        <input
          type="text"
          placeholder="Product name (e.g. oversized graphic tee) or a product URL"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={loading}
        />
        <button type="submit" className="primary-btn" disabled={loading || (!value.trim() && !imageBase64)}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      <div className="image-upload-row">
        <label className="image-upload-label">
          <CameraIcon />
          {imageName ? imageName : 'Optional: upload a product photo'}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleFileChange}
            disabled={loading}
            hidden
          />
        </label>
        {imageBase64 && (
          <button type="button" className="clear-image-btn" onClick={clearImage} disabled={loading}>
            Remove photo
          </button>
        )}
      </div>
      {imageError && <div className="image-upload-error">{imageError}</div>}
      {imageBase64 && <img src={imageBase64} alt="Uploaded product" className="image-upload-preview" />}
    </form>
  )
}

function CameraIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="14" r="3.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  )
}
