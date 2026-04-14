export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = localStorage.getItem('doorae_token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return fetch(url, { ...init, headers })
}
