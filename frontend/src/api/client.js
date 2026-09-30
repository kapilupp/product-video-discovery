import axios from 'axios'

const client = axios.create({ baseURL: '/api' })

export async function createSearch({ queryText, productUrl, imageBase64 }) {
  const { data } = await client.post('/search', {
    query_text: queryText || null,
    product_url: productUrl || null,
    image_base64: imageBase64 || null,
  })
  return data
}

export async function getSearch(searchId) {
  const { data } = await client.get(`/search/${searchId}`)
  return data
}

export async function getHistory() {
  const { data } = await client.get('/history')
  return data
}

export function streamSearchProgress(searchId, onEvent) {
  const source = new EventSource(`/api/search/${searchId}/stream`)
  source.onmessage = (e) => onEvent(JSON.parse(e.data))
  source.addEventListener('done', () => source.close())
  source.onerror = () => source.close()
  return () => source.close()
}
