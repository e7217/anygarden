# Markdown Preview for Chat Messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 채팅 메시지(에이전트 + 사용자)에 GFM 마크다운 렌더링을 적용한다.

**Architecture:** `react-markdown` + `remark-gfm`으로 마크다운을 React 엘리먼트 트리로 변환. 커스텀 컴포넌트 매핑으로 DESIGN.md 팔레트를 적용. MessageBubble에서 `<p>{content}</p>`를 `<MarkdownContent>`로 교체.

**Tech Stack:** react-markdown, remark-gfm, React 19, Tailwind CSS 4, TypeScript

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `anygarden-server/frontend/package.json` | Modify | `react-markdown`, `remark-gfm` 의존성 추가 |
| `anygarden-server/frontend/src/components/MarkdownContent.tsx` | Create | 마크다운 → React 렌더링 컴포넌트 (커스텀 컴포넌트 매핑) |
| `anygarden-server/frontend/src/components/MessageBubble.tsx` | Modify | plain text `<p>` → `<MarkdownContent>` 교체 |
| `anygarden-server/frontend/src/index.css` | Modify | 마크다운 요소 타이포그래피 스타일 추가 |

---

### Task 1: Install Dependencies

**Files:**
- Modify: `anygarden-server/frontend/package.json`

- [ ] **Step 1: Install react-markdown and remark-gfm**

```bash
cd anygarden-server/frontend && npm install react-markdown remark-gfm
```

- [ ] **Step 2: Verify TypeScript resolves the packages**

```bash
cd anygarden-server/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: No errors related to `react-markdown` or `remark-gfm`.

---

### Task 2: Add Markdown CSS Styles

마크다운 요소의 스타일을 DESIGN.md 팔레트에 맞춰 정의한다. MarkdownContent 컴포넌트보다 먼저 작성하여 렌더링 시 즉시 스타일이 적용되게 한다.

**Files:**
- Modify: `anygarden-server/frontend/src/index.css` (파일 끝에 추가)

- [ ] **Step 1: Add markdown prose styles to index.css**

`index.css` 파일 끝(`@utility text-badge` 블록 뒤)에 다음을 추가:

```css
/* ==========================================================================
   Markdown prose — chat bubble content (DESIGN.md compliant)
   ========================================================================== */

.markdown-prose {
  font-size: 0.875rem;           /* 14px — matches existing text-sm */
  line-height: 1.625;
  color: var(--color-foreground);
  word-break: break-word;
}

/* Paragraphs */
.markdown-prose p {
  margin: 0;
}
.markdown-prose p + p {
  margin-top: 0.5em;
}

/* Headings — scaled down for bubble context */
.markdown-prose h1 {
  font-size: 1rem;               /* 16px */
  font-weight: 700;
  line-height: 1.3;
  letter-spacing: -0.01em;
  margin: 0.75em 0 0.25em;
}
.markdown-prose h2 {
  font-size: 0.9375rem;          /* 15px */
  font-weight: 700;
  line-height: 1.3;
  margin: 0.75em 0 0.25em;
}
.markdown-prose h3 {
  font-size: 0.875rem;           /* 14px */
  font-weight: 700;
  line-height: 1.4;
  margin: 0.5em 0 0.25em;
}
.markdown-prose :first-child {
  margin-top: 0;
}

/* Inline code */
.markdown-prose code:not(pre code) {
  background: var(--color-surface-alt);
  padding: 0.125em 0.3em;
  border-radius: var(--radius-xs);
  font-size: 0.8125rem;          /* 13px */
  font-family: ui-monospace, "SF Mono", "Menlo", "Consolas", monospace;
}

/* Code block */
.markdown-prose pre {
  background: var(--color-surface-dark);
  color: #e8e6e3;
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  overflow-x: auto;
  margin: 0.5em 0;
  font-size: 0.8125rem;
  line-height: 1.5;
}
.markdown-prose pre code {
  background: none;
  padding: 0;
  border-radius: 0;
  font-size: inherit;
  color: inherit;
}

/* Links */
.markdown-prose a {
  color: var(--color-brand);
  text-decoration: none;
}
.markdown-prose a:hover {
  text-decoration: underline;
}

/* Lists */
.markdown-prose ul,
.markdown-prose ol {
  margin: 0.25em 0;
  padding-left: 1.5em;
}
.markdown-prose ul { list-style-type: disc; }
.markdown-prose ol { list-style-type: decimal; }
.markdown-prose li { margin: 0.125em 0; }
.markdown-prose li > p { margin: 0; }

/* Checklist (GFM task list) */
.markdown-prose ul:has(> li > input[type="checkbox"]) {
  list-style-type: none;
  padding-left: 0.25em;
}
.markdown-prose li > input[type="checkbox"] {
  margin-right: 0.4em;
  accent-color: var(--color-brand);
}

/* Blockquote */
.markdown-prose blockquote {
  border-left: 2px solid var(--color-border);
  padding-left: 0.75em;
  margin: 0.5em 0;
  color: var(--color-foreground-muted);
}

/* Table */
.markdown-prose table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5em 0;
  font-size: 0.8125rem;
}
.markdown-prose th {
  background: var(--color-surface-alt);
  font-weight: 600;
  text-align: left;
  padding: 0.375rem 0.5rem;
  border: 1px solid var(--color-border);
}
.markdown-prose td {
  padding: 0.375rem 0.5rem;
  border: 1px solid var(--color-border);
}

/* Horizontal rule */
.markdown-prose hr {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 0.75em 0;
}

/* Strikethrough */
.markdown-prose del {
  color: var(--color-foreground-muted);
}

/* Strong & emphasis */
.markdown-prose strong { font-weight: 600; }
.markdown-prose em { font-style: italic; }

/* Images — constrain within bubble */
.markdown-prose img {
  max-width: 100%;
  border-radius: var(--radius-md);
  margin: 0.5em 0;
}
```

- [ ] **Step 2: Verify build still compiles**

```bash
cd anygarden-server/frontend && npx tsc --noEmit
```

Expected: No errors.

---

### Task 3: Create MarkdownContent Component

**Files:**
- Create: `anygarden-server/frontend/src/components/MarkdownContent.tsx`

- [ ] **Step 1: Create MarkdownContent.tsx**

```tsx
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownContentProps {
  content: string
}

export default function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <div className="markdown-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Open links in new tab
          a: ({ children, href, ...props }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd anygarden-server/frontend && npx tsc --noEmit
```

Expected: No errors.

---

### Task 4: Integrate into MessageBubble

**Files:**
- Modify: `anygarden-server/frontend/src/components/MessageBubble.tsx`

- [ ] **Step 1: Replace plain text with MarkdownContent**

In `MessageBubble.tsx`, add the import at the top:

```tsx
import MarkdownContent from '@/components/MarkdownContent'
```

Replace both `<p>` blocks. The "mine" message (line 34-36):

```tsx
// Before:
<p className="text-sm leading-relaxed whitespace-pre-wrap break-words text-[var(--color-foreground)]">
  {message.content}
</p>

// After:
<MarkdownContent content={message.content} />
```

The "other" message (line 58-60):

```tsx
// Before:
<p className="text-sm leading-relaxed whitespace-pre-wrap break-words text-[var(--color-foreground)]">
  {message.content}
</p>

// After:
<MarkdownContent content={message.content} />
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd anygarden-server/frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 3: Verify Vite build succeeds**

```bash
cd anygarden-server/frontend && npm run build
```

Expected: Build succeeds, output in `anygarden-server/anygarden/static/`.

---

### Task 5: Manual Verification

- [ ] **Step 1: Start backend server**

```bash
cd anygarden-server && uv run anygarden-server --host 0.0.0.0 --port 8001 &
```

- [ ] **Step 2: Start Vite dev server**

```bash
cd anygarden-server/frontend && npm run dev
```

- [ ] **Step 3: Test markdown rendering in the chat UI**

Open `http://localhost:5173` in a browser. Join a room and send test messages:

1. **Inline formatting**: `This is **bold** and *italic* and ~~strikethrough~~`
2. **Inline code**: `` Use `console.log()` for debugging ``
3. **Code block**:
   ````
   ```python
   def hello():
       print("world")
   ```
   ````
4. **List**: `- item 1\n- item 2\n- item 3`
5. **Link**: `Check [Anygarden](https://example.com)`
6. **Table**: `| A | B |\n|---|---|\n| 1 | 2 |`
7. **Blockquote**: `> This is a quote`
8. **Plain text** (no markdown): `Hello, this is a normal message` — should look identical to before.

Verify each renders correctly with DESIGN.md styling: warm neutrals, Notion Blue links, warm dark code blocks.

- [ ] **Step 4: Test both "mine" and "other" message bubbles**

Verify markdown renders correctly in both right-aligned (mine) and left-aligned (other) message bubbles.
