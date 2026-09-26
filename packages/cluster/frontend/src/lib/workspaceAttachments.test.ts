import { expect, it } from 'vitest'
import { workspaceCliPrefix } from './workspaceAttachments'

it('uses the advertised data directory and requires an explicit directory when it is unknown', () => {
  expect(workspaceCliPrefix('integrated', "/srv/node's data")).toBe("anygarden-machine workspace --node-data-dir '/srv/node'\"'\"'s data'")
  expect(workspaceCliPrefix('integrated')).toContain('${ANYGARDEN_NODE_DATA_DIR:?')
  expect(workspaceCliPrefix('integrated')).not.toContain('~/.anygarden')
  expect(workspaceCliPrefix('remote', '/srv/node')).toBe('anygarden-machine workspace')
})
