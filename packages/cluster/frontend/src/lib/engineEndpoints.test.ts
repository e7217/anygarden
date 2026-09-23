import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { defaultEndpointProtocol, discoverEndpointModels, isValidEndpointUrl } from './engineEndpoints'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => vi.resetAllMocks())

describe('engine endpoint helpers (#685)', () => {
  it('accepts plain http(s) base URLs only', () => {
    expect(isValidEndpointUrl('http://10.0.0.5:8000/v1')).toBe(true)
    expect(isValidEndpointUrl('https://models.internal/v1')).toBe(true)
    for (const bad of ['', 'ftp://h/v1', 'http://u:p@h/v1', 'http://h/v1?k=1', 'http://h/v1#x', 'not a url', 'http://h /v1']) {
      expect(isValidEndpointUrl(bad)).toBe(false)
    }
  })

  it('defaults Pi to Chat Completions and Codex to Responses', () => {
    expect(defaultEndpointProtocol('pi-cli')).toBe('chat-completions')
    expect(defaultEndpointProtocol('codex-cli')).toBe('responses')
  })

  it('posts the probe request and surfaces server detail on failure', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({
      models: [{ id: 'qwen', max_model_len: 4096 }], reachable_from: 'server',
    })))
    const result = await discoverEndpointModels({ base_url: 'http://h/v1' })
    expect(result.models[0].id).toBe('qwen')
    expect(vi.mocked(apiFetch).mock.calls[0][0]).toBe('/api/v1/engine-endpoints/models')
    expect(JSON.parse(String(vi.mocked(apiFetch).mock.calls[0][1]?.body))).toEqual({ base_url: 'http://h/v1' })

    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Could not reach the model server from the AnyGarden server' }), { status: 502 }))
    await expect(discoverEndpointModels({ base_url: 'http://h/v1' })).rejects.toThrow('Could not reach')
  })
})
