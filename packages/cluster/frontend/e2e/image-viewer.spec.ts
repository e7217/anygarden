import { deflateSync } from 'node:zlib'
import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

/**
 * Attachment image viewer zoom and pan (#882), in a real browser: jsdom has
 * no layout, so fit size, actual size and pan bounds are only meaningful
 * here.
 *
 * Like ``chat-width.spec.ts`` this runs against Vite alone with REST and
 * WebSocket stubbed. An agent reply carries one 1600×1200 image, larger
 * than the viewer, so "actual size" is a real zoom step.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const AGENT_PARTICIPANT = 'part-agent'
const WIDTH = 1600
const HEIGHT = 1200

/** A solid grey RGB PNG, built here rather than checked in. */
function png(width: number, height: number): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc = (bytes: Buffer) => {
    let c = 0xffffffff
    for (const byte of bytes) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const sum = Buffer.alloc(4)
    sum.writeUInt32BE(crc(body))
    return Buffer.concat([length, body, sum])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header.set([8, 2, 0, 0, 0], 8) // 8-bit RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, 0xd0)])
  const pixels = deflateSync(Buffer.concat(Array.from({ length: height }, () => row)))
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', pixels),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

const IMAGE = png(WIDTH, HEIGHT)

const HISTORY = [
  {
    id: 'msg-chart',
    room_id: ROOM_ID,
    participant_id: AGENT_PARTICIPANT,
    content: 'here is the chart',
    parent_message_id: null,
    root_message_id: null,
    seq: 1,
    created_at: '2026-10-19T00:00:01Z',
    metadata: {
      references: [
        {
          type: 'artifact',
          id: 'art-chart',
          room_id: ROOM_ID,
          name: 'chart.png',
          mime: 'image/png',
          size_bytes: IMAGE.length,
          sha256: 'sha',
        },
      ],
    },
  },
]

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'off' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.21.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    const room = { id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false, parent_room_id: null }
    if (pathname === '/api/v1/rooms') return fulfillJson(route, 200, [room])
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        ...room,
        participants: [
          { id: 'part-me', display_name: 'e2e', kind: 'user', user_id: user.id },
          { id: AGENT_PARTICIPANT, display_name: 'planner', kind: 'agent', agent_id: 'agent-1', online: false },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) return fulfillJson(route, 200, HISTORY)
    if (pathname === `/api/v1/rooms/${ROOM_ID}/artifacts/art-chart`) {
      return route.fulfill({ status: 200, contentType: 'image/png', body: IMAGE })
    }
    return fulfillJson(route, 200, [])
  })
}

async function stubWebSocket(page: Page) {
  await page.addInitScript(() => {
    class FakeWebSocket {
      static readonly OPEN = 1
      readyState = 1
      onopen: ((e: unknown) => void) | null = null
      onclose: ((e: unknown) => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      onmessage: ((e: unknown) => void) | null = null
      constructor() {
        setTimeout(() => this.onopen?.({}), 0)
      }
      send() {}
      close() {
        this.readyState = 3
      }
      addEventListener() {}
      removeEventListener() {}
    }
    ;(window as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
  })
}

async function openViewer(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)
  await page.getByRole('button', { name: 'Open chart.png' }).click()
  const viewport = page.getByTestId('image-zoom-viewport')
  const image = viewport.getByRole('img', { name: 'chart.png' })
  await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBe(WIDTH)
  return { viewport, image }
}

/** The image's on-screen box (transform included). */
async function box(locator: Locator) {
  const b = await locator.boundingBox()
  expect(b).not.toBeNull()
  return b!
}

const level = (page: Page) => page.getByRole('button', { name: /Actual size/ })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('anygarden_locale', 'en'))
  await page.setViewportSize({ width: 1280, height: 900 })
  await stubWebSocket(page)
  await stubApi(page)
})

test.describe('image viewer zoom (#882)', () => {
  test('wheel zooms around the cursor and the page does not scroll', async ({ page }) => {
    const { viewport, image } = await openViewer(page)
    const fitted = await box(image)
    const frame = await box(viewport)
    // Cursor over the image's left third.
    const cursor = { x: fitted.x + fitted.width / 3, y: fitted.y + fitted.height / 2 }
    const ratio = (cursor.x - fitted.x) / fitted.width

    await page.mouse.move(cursor.x, cursor.y)
    await page.mouse.wheel(0, -400)

    await expect.poll(async () => (await box(image)).width).toBeGreaterThan(fitted.width * 1.5)
    const zoomed = await box(image)
    // The image point under the cursor stayed under it.
    expect(Math.abs(zoomed.x + zoomed.width * ratio - cursor.x)).toBeLessThan(2)
    // Zoomed past the frame, the image still covers it (no drift out of view).
    expect(zoomed.x).toBeLessThanOrEqual(frame.x + 1)
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
  })

  test('drag moves a zoomed image only within its bounds', async ({ page }) => {
    const { viewport, image } = await openViewer(page)
    await viewport.focus()
    await page.keyboard.press('+')
    await page.keyboard.press('+')
    await page.keyboard.press('+')
    const frame = await box(viewport)
    await expect.poll(async () => (await box(image)).width).toBeGreaterThan(frame.width)
    // Key steps glide; a drag cuts the glide short and jumps to its end, so
    // measure only once the zoom has settled.
    await expect.poll(() => image.evaluate(el => el.getAnimations().length)).toBe(0)
    const before = await box(image)

    const start = { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 }
    await page.mouse.move(start.x, start.y)
    await page.mouse.down()
    await page.mouse.move(start.x + 60, start.y + 30, { steps: 4 })
    await page.mouse.up()
    const moved = await box(image)
    expect(moved.x - before.x).toBeCloseTo(60, 0)

    // A long drag stops with the image's left edge on the frame's.
    await page.mouse.move(start.x, start.y)
    await page.mouse.down()
    await page.mouse.move(start.x + 3000, start.y, { steps: 6 })
    await page.mouse.up()
    expect(Math.abs((await box(image)).x - frame.x)).toBeLessThan(2)
  })

  test('double click switches between fit and actual size', async ({ page }) => {
    const { viewport, image } = await openViewer(page)
    const fitted = await box(image)
    await expect(level(page)).not.toHaveText('100%')

    await viewport.dblclick()
    await expect(level(page)).toHaveText('100%')
    await expect.poll(async () => Math.round((await box(image)).width)).toBe(WIDTH)

    await viewport.dblclick()
    await expect.poll(async () => Math.round((await box(image)).width)).toBe(Math.round(fitted.width))
  })

  test('toolbar and keys zoom, and fit resets', async ({ page }) => {
    const { viewport, image } = await openViewer(page)
    const fitted = await box(image)
    await page.getByRole('button', { name: 'Zoom in' }).click()
    await expect.poll(async () => (await box(image)).width / fitted.width).toBeCloseTo(1.25, 1)

    await viewport.focus()
    await page.keyboard.press('0')
    await expect.poll(async () => (await box(image)).width / fitted.width).toBeCloseTo(1, 1)
    await expect(page.getByRole('button', { name: 'Fit to screen' })).toBeDisabled()

    // Reopening starts fitted again.
    await page.getByRole('button', { name: 'Zoom in' }).click()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Open chart.png' }).click()
    await expect(page.getByRole('button', { name: 'Fit to screen' })).toBeDisabled()
  })
})
