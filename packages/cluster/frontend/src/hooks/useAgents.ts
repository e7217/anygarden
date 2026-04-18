import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '@/lib/api';

export interface Agent {
  id: string;
  name: string;
  engine: string;
  desired_state: string;
  actual_state: string;
  placed_on_machine_id?: string;
  restart_policy: string;
  // Phase 0 file manifest. `agents_md` is the per-agent system
  // prompt / role / rules body the materializer writes into
  // ``agent_root/AGENTS.md``. Nullable so the admin can clear it.
  agents_md?: string | null;
  reasoning_effort?: string | null;
  model?: string | null;
  // Lifecycle populates this on crash or refused dispatch
  // (e.g. ``spawn_refused_no_rooms``). The admin-agents table
  // surfaces it as a tooltip on ``pending`` / ``crashed`` state
  // badges so admins can tell at a glance why an agent is stuck.
  last_crash_reason?: string | null;
  // Issue #101 — admin-chosen avatar override. Both NULL means
  // the UI falls back to the seed-driven initial from EntityAvatar.
  avatar_kind?: string | null;
  avatar_value?: string | null;
}

export interface EngineModel {
  id: string;
  label: string;
  reasoning_levels: string[];
}

export interface EngineCatalog {
  engine: string;
  default_model: string;
  models: EngineModel[];
  reasoning_levels: string[];
}

export interface AvailableEngine {
  engine: string;
  machine_count: number;
}

export interface AgentFile {
  path: string;
  content: string;
  updated_at: string;
}

// Read-only snapshot of a library skill attached to an agent.
// Issue #133 — manifest dialog surfaces these as a separate
// read-only section so admins can see which library skills the
// agent actually carries at spawn time, without confusing them
// with the editable ``AgentFile`` rows.
export interface AttachedSkill {
  id: string;
  name: string;
  source: string;
  pinned_rev: string;
  // Paths of extra bundled files (not including SKILL.md). Shown as
  // a list; bodies are not yet available through the preview API.
  extra_files: string[];
}

export interface SkillPreview {
  id: string;
  name: string;
  skill_md: string;
  extra_files: string[];
}

export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);

  const fetchAgents = useCallback(async () => {
    const resp = await apiFetch('/api/v1/agents');
    if (resp.ok) setAgents(await resp.json());
  }, []);

  const createAgent = useCallback(async (data: {
    name: string;
    engine: string;
    rooms?: string[];
    agents_md?: string;
    files?: Record<string, string>;
    reasoning_effort?: string;
    model?: string;
  }) => {
    const resp = await apiFetch('/api/v1/agents', {
      method: 'POST',
      body: JSON.stringify(data),
    });
    if (resp.ok) { await fetchAgents(); return await resp.json(); }
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to create agent');
  }, [fetchAgents]);

  const deleteAgent = useCallback(async (id: string) => {
    await apiFetch(`/api/v1/agents/${id}`, { method: 'DELETE' });
    // Remove from local state immediately
    setAgents(prev => prev.filter(a => a.id !== id));
  }, []);

  const startAgent = useCallback(async (id: string) => {
    const resp = await apiFetch(`/api/v1/agents/${id}/start`, { method: 'POST' });
    if (resp.ok) { await fetchAgents(); return await resp.json(); }
    throw new Error('Failed to start agent');
  }, [fetchAgents]);

  const stopAgent = useCallback(async (id: string) => {
    const resp = await apiFetch(`/api/v1/agents/${id}/stop`, { method: 'POST' });
    if (resp.ok) { await fetchAgents(); }
  }, [fetchAgents]);

  const addAgentToRoom = useCallback(async (agentId: string, roomId: string) => {
    const resp = await apiFetch(`/api/v1/agents/${agentId}/rooms`, {
      method: 'POST',
      body: JSON.stringify({ room_id: roomId }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to add agent to room');
    }
    await fetchAgents();
  }, [fetchAgents]);

  const removeAgentFromRoom = useCallback(async (agentId: string, roomId: string) => {
    const resp = await apiFetch(`/api/v1/agents/${agentId}/rooms/${roomId}`, {
      method: 'DELETE',
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to remove agent from room');
    }
    await fetchAgents();
  }, [fetchAgents]);

  // ── Agent manifest editing ───────────────────────────────────────
  //
  // Thin wrappers around the Phase-0 REST surface:
  //   PUT    /api/v1/agents/{id}        — name + agents_md
  //   GET    /api/v1/agents/{id}/files  — list AgentFile rows
  //   PUT    /api/v1/agents/{id}/files  — upsert {path, content}
  //   DELETE /api/v1/agents/{id}/files  — delete by path (body)
  //
  // These are return-value based (instead of updating local state)
  // because the edit dialog owns its own working copy of the file
  // tree and flushes changes in bulk on Save.

  const updateAgent = useCallback(async (
    id: string,
    patch: {
      name?: string;
      agents_md?: string | null;
      agents_md_set?: boolean;
      // Issue #101 — admin-chosen avatar override. Follows the same
      // ``*_set`` idiom as ``agents_md`` to distinguish "omit the
      // field" from "clear to null".
      avatar_kind?: string | null;
      avatar_kind_set?: boolean;
      avatar_value?: string | null;
      avatar_value_set?: boolean;
    },
  ) => {
    const resp = await apiFetch(`/api/v1/agents/${id}`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to update agent');
    }
    await fetchAgents();
    return await resp.json() as Agent;
  }, [fetchAgents]);

  const fetchAgentFiles = useCallback(async (id: string): Promise<AgentFile[]> => {
    const resp = await apiFetch(`/api/v1/agents/${id}/files`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to list agent files');
    }
    return await resp.json();
  }, []);

  const upsertAgentFile = useCallback(async (
    id: string,
    path: string,
    content: string,
  ): Promise<AgentFile> => {
    const resp = await apiFetch(`/api/v1/agents/${id}/files`, {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to save file');
    }
    return await resp.json();
  }, []);

  const deleteAgentFile = useCallback(async (id: string, path: string) => {
    const resp = await apiFetch(`/api/v1/agents/${id}/files`, {
      method: 'DELETE',
      body: JSON.stringify({ path }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to delete file');
    }
  }, []);

  // Issue #133 — attached library skills for the manifest dialog.
  // Reuses the admin skills endpoint with a client-side filter on
  // ``attached_agent_ids``; keeps the server API unchanged.
  const fetchAttachedSkills = useCallback(async (
    agentId: string,
  ): Promise<AttachedSkill[]> => {
    const resp = await apiFetch('/api/v1/admin/skills');
    if (!resp.ok) return [];
    const rows: Array<{
      id: string;
      name: string;
      source: string;
      pinned_rev: string;
      scripts_detected: string[];
      attached_agent_ids: string[];
      status: string;
    }> = await resp.json();
    return rows
      .filter(r => r.status === 'approved' && r.attached_agent_ids.includes(agentId))
      .map(r => ({
        id: r.id,
        name: r.name,
        source: r.source,
        pinned_rev: r.pinned_rev,
        extra_files: r.scripts_detected,
      }));
  }, []);

  // Fetch SKILL.md body for readonly viewing. Lazy — only called
  // when the admin actually selects a skill file node.
  const fetchSkillPreview = useCallback(async (
    skillId: string,
  ): Promise<SkillPreview | null> => {
    const resp = await apiFetch(`/api/v1/admin/skills/${skillId}/preview`);
    if (!resp.ok) return null;
    return await resp.json();
  }, []);

  const [availableEngines, setAvailableEngines] = useState<AvailableEngine[]>([]);

  const fetchAvailableEngines = useCallback(async () => {
    const resp = await apiFetch('/api/v1/agents/engines/available');
    if (resp.ok) setAvailableEngines(await resp.json());
  }, []);

  // Fetch model catalog for a specific engine. Returns null when the
  // engine is not in the static catalog (e.g. a custom "echo" engine
  // running on a dev machine), letting the caller skip the model
  // dropdown entirely instead of displaying an empty one.
  const fetchEngineCatalog = useCallback(async (engineName: string): Promise<EngineCatalog | null> => {
    const resp = await apiFetch(`/api/v1/agents/engines/${encodeURIComponent(engineName)}/models`);
    if (resp.ok) return await resp.json();
    return null;
  }, []);

  useEffect(() => { fetchAgents(); fetchAvailableEngines(); }, [fetchAgents, fetchAvailableEngines]);
  return {
    agents,
    availableEngines,
    fetchAvailableEngines,
    fetchEngineCatalog,
    createAgent,
    deleteAgent,
    startAgent,
    stopAgent,
    addAgentToRoom,
    removeAgentFromRoom,
    fetchAgents,
    updateAgent,
    fetchAgentFiles,
    upsertAgentFile,
    deleteAgentFile,
    fetchAttachedSkills,
    fetchSkillPreview,
  };
}
