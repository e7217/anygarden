import { useState, useEffect, useCallback } from 'react'
import { Plus, Trash2, CheckCircle2, Circle, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'

interface Task {
  id: string
  room_id: string
  title: string
  status: string
  assignee_participant_id: string | null
  created_at: string
}

interface TaskPanelProps {
  roomId: string
}

const STATUS_CYCLE = ['todo', 'in_progress', 'done'] as const
const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
}
const STATUS_LABEL: Record<string, string> = {
  todo: 'Todo',
  in_progress: 'In Progress',
  done: 'Done',
}

export default function TaskPanel({ roomId }: TaskPanelProps) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [filter, setFilter] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [adding, setAdding] = useState(false)

  const fetchTasks = useCallback(async () => {
    const params = filter ? `?status=${filter}` : ''
    const resp = await apiFetch(`/api/v1/rooms/${roomId}/tasks${params}`)
    if (resp.ok) setTasks(await resp.json())
    else setTasks([])
  }, [roomId, filter])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  const createTask = async () => {
    if (!newTitle.trim()) return
    setAdding(true)
    await apiFetch(`/api/v1/rooms/${roomId}/tasks`, {
      method: 'POST',
      body: JSON.stringify({ title: newTitle.trim() }),
    })
    setNewTitle('')
    setAdding(false)
    fetchTasks()
  }

  const cycleStatus = async (task: Task) => {
    const idx = STATUS_CYCLE.indexOf(task.status as typeof STATUS_CYCLE[number])
    const next = STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length]
    await apiFetch(`/api/v1/tasks/${task.id}`, {
      method: 'PUT',
      body: JSON.stringify({ status: next }),
    })
    fetchTasks()
  }

  const deleteTask = async (id: string) => {
    await apiFetch(`/api/v1/tasks/${id}`, { method: 'DELETE' })
    fetchTasks()
  }

  const filters = [
    { key: null, label: 'All' },
    { key: 'todo', label: 'Todo' },
    { key: 'in_progress', label: 'In Progress' },
    { key: 'done', label: 'Done' },
  ]

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Filter tabs */}
      <div className="flex items-center gap-1 border-b border-[var(--color-border)] px-4 py-2">
        {filters.map(f => (
          <button
            key={f.key ?? 'all'}
            onClick={() => setFilter(f.key)}
            className={`rounded-[var(--radius-sm)] px-2.5 py-1 text-xs transition-colors ${
              filter === f.key
                ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand)] font-medium'
                : 'text-[var(--color-foreground-muted)] hover:bg-black/5'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto px-4 py-2 space-y-1">
        {tasks.map(task => {
          const Icon = STATUS_ICON[task.status] ?? Circle
          return (
            <div key={task.id} className="group flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]">
              <button onClick={() => cycleStatus(task)} title={`Status: ${STATUS_LABEL[task.status] ?? task.status}`}>
                <Icon className={`h-4 w-4 ${task.status === 'done' ? 'text-green-600' : task.status === 'in_progress' ? 'text-[var(--color-brand)]' : 'text-[var(--color-foreground-subtle)]'}`} />
              </button>
              <span className={`flex-1 text-sm ${task.status === 'done' ? 'line-through text-[var(--color-foreground-muted)]' : 'text-[var(--color-foreground)]'}`}>
                {task.title}
              </span>
              <button
                onClick={() => deleteTask(task.id)}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-50 text-red-400 hover:text-red-600 transition-all"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
        {tasks.length === 0 && (
          <div className="py-8 text-center text-sm text-[var(--color-foreground-muted)]">
            No tasks yet
          </div>
        )}
      </div>

      {/* Add task */}
      <div className="border-t border-[var(--color-border)] px-4 py-2">
        <div className="flex items-center gap-2">
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createTask()}
            placeholder="Add a task..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-foreground-subtle)]"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={createTask}
            disabled={adding || !newTitle.trim()}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
