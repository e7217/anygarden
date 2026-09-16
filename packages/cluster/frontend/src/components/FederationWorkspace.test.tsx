// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

import FederationWorkspace from './FederationWorkspace'
import { initialFederationScenario, type FederationScenario } from '@/lib/federationMock'

afterEach(cleanup)

function scenario(overrides: Partial<FederationScenario> = {}): FederationScenario {
  return {
    ...initialFederationScenario,
    localNode: { ...initialFederationScenario.localNode },
    remoteNode: { ...initialFederationScenario.remoteNode },
    agents: initialFederationScenario.agents.map((agent) => ({ ...agent })),
    ...overrides,
  }
}

function selectTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false })
}

describe('FederationWorkspace mock UI', () => {
  it('labels the preview as mock-only and never exposes an unshared remote agent', () => {
    render(<FederationWorkspace />)

    expect(screen.getByText('Interactive mock · no server connection')).toBeInTheDocument()
    expect(screen.queryByText('Private reviewer')).not.toBeInTheDocument()

    selectTab('2. Shared channel')
    expect(screen.getByText('Planner')).toBeInTheDocument()
    expect(screen.queryByText('Builder')).not.toBeInTheDocument()
    expect(screen.queryByText('Private reviewer')).not.toBeInTheDocument()
  })

  it('accepts an invitation locally, emits an adapter intent, and reveals only shared agents', () => {
    const onIntent = vi.fn()
    render(<FederationWorkspace onIntent={onIntent} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview accept' }))
    expect(screen.getByText('Accepted locally · waiting for peer')).toBeInTheDocument()
    expect(onIntent).toHaveBeenCalledWith({ type: 'accept_node_invite', nodeId: 'node-orchard' })

    selectTab('2. Shared channel')
    expect(screen.queryByText('Builder')).not.toBeInTheDocument()

    selectTab('1. Connection')
    fireEvent.click(screen.getByRole('button', { name: 'Preview peer confirmation' }))
    expect(screen.getByText('Connected')).toBeInTheDocument()
    selectTab('2. Shared channel')
    expect(screen.getByText('Builder')).toBeInTheDocument()
    expect(screen.queryByText('Private reviewer')).not.toBeInTheDocument()
  })

  it('distinguishes channel-owner downtime from execution-node downtime', () => {
    render(<FederationWorkspace initialScenario={scenario({
      localNode: { ...initialFederationScenario.localNode, reachability: 'offline' },
      remoteNode: { ...initialFederationScenario.remoteNode, connection: 'accepted', acknowledgement: 'confirmed' },
    })} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Channel owner is offline')
    expect(screen.getByRole('alert')).toHaveTextContent('shared changes remain unconfirmed')

    fireEvent.click(screen.getByRole('button', { name: 'Toggle owner node' }))
    fireEvent.click(screen.getByRole('button', { name: 'Toggle execution node' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Execution node is offline')
    expect(screen.getByRole('alert')).toHaveTextContent('will not be retried automatically')
  })

  it('keeps acceptance, execution, completion, stop request, and stop confirmation distinct', () => {
    const onIntent = vi.fn()
    render(<FederationWorkspace initialScenario={scenario({
      remoteNode: { ...initialFederationScenario.remoteNode, connection: 'accepted', acknowledgement: 'confirmed', sharedChannels: 1 },
    })} onIntent={onIntent} />)

    selectTab('3. Task handoff')
    fireEvent.click(screen.getByRole('button', { name: /Preview task request/ }))
    expect(screen.getAllByText('Waiting for the execution node')).not.toHaveLength(0)
    expect(onIntent).toHaveBeenCalledWith({
      type: 'request_delegation',
      agentId: 'builder',
      nodeId: 'node-orchard',
    })
    fireEvent.click(screen.getByRole('button', { name: /Preview remote acceptance/ }))
    expect(screen.getAllByText('Accepted — execution has not started')).not.toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: /Preview execution start/ }))
    expect(screen.getAllByText('Running on the remote node')).not.toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Preview stop request' }))
    expect(screen.getAllByText('Stop requested — execution may still be active')).not.toHaveLength(0)
    expect(screen.getByText('Stop is not confirmed yet.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Preview stop confirmation' }))
    expect(screen.getAllByText('Stopped and cancellation confirmed')).not.toHaveLength(0)
    expect(screen.getByText('Execution is confirmed stopped.')).toBeInTheDocument()
    expect(onIntent).toHaveBeenCalledWith({ type: 'request_cancel' })
    expect(onIntent).toHaveBeenCalledWith({ type: 'confirm_stop' })
  })

  it('shows remote rejection as terminal without implying execution', () => {
    render(<FederationWorkspace initialScenario={scenario({
      remoteNode: { ...initialFederationScenario.remoteNode, connection: 'accepted', acknowledgement: 'confirmed', sharedChannels: 1 },
      delegationState: 'requested',
    })} />)

    selectTab('3. Task handoff')
    fireEvent.click(screen.getByRole('button', { name: 'Preview remote rejection' }))
    expect(screen.getAllByText('Request declined by the execution node')).not.toHaveLength(0)
    expect(screen.getByText(/Task · todo · process · not started/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request was declined' })).toBeDisabled()
  })

  it('keeps an owner-offline submission unconfirmed until authority returns', () => {
    render(<FederationWorkspace initialScenario={scenario({
      localNode: { ...initialFederationScenario.localNode, reachability: 'offline' },
      remoteNode: { ...initialFederationScenario.remoteNode, connection: 'accepted', acknowledgement: 'confirmed', sharedChannels: 1 },
    })} />)

    selectTab('3. Task handoff')
    fireEvent.click(screen.getByRole('button', { name: /Preview task request/ }))
    expect(screen.getAllByText('Submitted locally — not confirmed by the channel owner')).not.toHaveLength(0)
    expect(screen.getByText('This request exists only on your node.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Preview authority confirmation/ })).toBeDisabled()
  })

  it('separates a known terminal failure from an unknown execution outcome', () => {
    render(<FederationWorkspace initialScenario={scenario({
      remoteNode: { ...initialFederationScenario.remoteNode, connection: 'accepted', acknowledgement: 'confirmed', sharedChannels: 1 },
      delegationState: 'running',
      processState: 'running',
      taskStatus: 'in_progress',
    })} />)

    selectTab('3. Task handoff')
    fireEvent.click(screen.getByRole('button', { name: 'Preview known failure' }))
    expect(screen.getAllByText('Execution failed and termination is confirmed')).not.toHaveLength(0)
    expect(screen.getByText(/ENGINE_ERROR/)).toBeInTheDocument()
    expect(screen.getByText(/Task · failed · process · finished/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Task failed' })).toBeDisabled()
  })
})
