# Anygarden UI — build conventions

Notion-inspired **warm-neutral** design system (React 19 + Radix primitives + Tailwind v4).
Single accent: **Notion Blue `#0075de`**. Text is **near-black `rgba(0,0,0,0.95)`**, never pure black.
Borders are whisper-weight `1px solid rgba(0,0,0,0.1)`; shadows are sub-0.05 opacity.

## Setup — no provider needed

These are self-contained primitives. There is **no ThemeProvider/context to wrap** — design
tokens are global CSS custom properties defined in the shipped stylesheet, so a component renders
on-brand as soon as the stylesheet is present. Just compose the components.

- Components are on `window.AnygardenUI.*` (the bundle); the styles load from the linked `styles.css`.
- `Dialog` is controlled by Radix — render with `open` (and `onOpenChange`) to show it.
- Compound parts are separate exports: `Card`+`CardHeader/CardTitle/CardDescription/CardContent/CardFooter`,
  `Table`+`TableHeader/TableBody/TableRow/TableHead/TableCell/TableCaption`,
  `Tabs`+`TabsList/TabsTrigger/TabsContent`, `Dialog`+`DialogContent/DialogHeader/DialogTitle/DialogDescription/DialogFooter`,
  `Avatar`+`AvatarImage/AvatarFallback`, `ChatBubble`+`ChatBubbleAvatar/ChatBubbleMessage/ChatBubbleTimestamp`.

## Styling idiom — className + CSS-variable tokens

Components take a `className` (Tailwind v4 utilities) and DS-specific props (`variant`, `size`).
For your **own layout glue**, style with the design tokens below — never invent hex values or ad-hoc
spacing. Reference them as `var(--token)` in `style={{…}}` or via the matching Tailwind class.

**Color** (`var(--color-*)`):
`background` · `foreground` (near-black) · `foreground-muted` (secondary text) · `foreground-subtle`
· `surface` · `surface-alt` (warm off-white sections) · `surface-dark`
· `brand` (`#0075de`, the only accent) · `brand-hover` · `brand-tint-bg` / `brand-tint-text` (pill badges)
· `success` · `warning` · `danger` (`#c83a2b`, destructive) · `border` (whisper) · `border-strong` (inputs)
· `status-online` · `tone-1..8` / `tone-N-fg` (seeded avatar tints).

**Radius** (`var(--radius*)`): `--radius` 4px (buttons/inputs) · `-sm` 5px · `-md` 8px · `-lg` 12px (cards/dialogs) · `-xl` 16px · `-pill`.
**Spacing** (`var(--space-N)`): 1=4px 2=8px 3=12px 4=16px 5=20px 6=24px (canonical card/dialog/section pad) 8=32px 12=48px.
**Shadow** (`var(--shadow-*)`): `whisper` · `card` (standard elevation) · `deep` (dialogs/popovers) · `focus`.
**Type utilities** (class — sets size + weight + line-height + tracking together; color is NOT set, apply `var(--color-foreground*)` separately): `text-display` 48 · `text-title` 32 · `text-heading` 24 · `text-lead` 20 · `text-caption` 14 · `text-badge` 12. Body is 16px Inter.
**Custom utilities**: `shadow-card` · `shadow-deep` · `shadow-whisper` · `surface-alt`.
**Font**: Inter (`var(--font-sans)`), loaded via the stylesheet.

## Compound parts & key patterns

- **Compound components** — sub-parts are separate exports on `window.AnygardenUI.*`, composed inside the root:
  - `Card` → `CardHeader` · `CardTitle` · `CardDescription` · `CardContent` · `CardFooter` (Header/Content/Footer carry the `p-6` padding; Title/Description carry the type).
  - `Table` → `TableHeader` · `TableBody` · `TableFooter` · `TableRow` · `TableHead` · `TableCell` · `TableCaption`. A `TableRow` supports `data-state="selected"` (brand-tint highlight).
  - `Tabs` → `TabsList` · `TabsTrigger` · `TabsContent` (a trigger and its panel are linked by matching `value`).
  - `Dialog` → `DialogTrigger` · `DialogContent` (which wraps `DialogHeader`/`DialogTitle`/`DialogDescription`/`DialogFooter`).
  - `Avatar` → `AvatarImage` + `AvatarFallback` — **Avatar renders nothing without one of these children.**
  - `ChatBubble` → `ChatBubbleAvatar` · `ChatBubbleMessage` · `ChatBubbleTimestamp` · `ChatBubbleAction`. Set `variant`/`layout` once on `ChatBubble`; it auto-injects them into the children.
- **Controlled state** — `Dialog` and `Tabs` are Radix-controlled. Pass `open`+`onOpenChange` (with a `DialogTrigger`/`useState`) and `value`+`onValueChange` so they can actually open and dismiss; a hardcoded `open` with no handler can't be closed. `Input`/`ChatInput` use `value`+`onChange`.
- **ChatMessageList** fills its parent (`h-full`) and scrolls internally — place it in a height-constrained box (fixed height, or a flex child with `min-h-0`). It auto-scrolls to the newest child; `smooth` animates that scroll.
- **Accessibility** — icon-only buttons (`Button size="icon"`, `ChatBubbleAction`) require an `aria-label`. `Dialog` should keep a `DialogTitle` (Radix wires it as the accessible name).

## Where the truth lives

- `_ds_bundle.css` (imported by `styles.css`) — every token's real value and the component styles.
- Each `<Name>.prompt.md` + `<Name>.d.ts` — that component's API and usage.

## Idiomatic example

```tsx
<Card>
  <CardHeader>
    <CardTitle>Production cluster</CardTitle>
    <CardDescription>3 agents · 2 machines online</CardDescription>
  </CardHeader>
  <CardContent>
    <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
      <Badge>online</Badge>
      <span style={{ color: 'var(--color-foreground-muted)' }}>last activity 4m ago</span>
    </div>
  </CardContent>
  <CardFooter style={{ gap: 'var(--space-2)' }}>
    <Button size="sm">Open room</Button>
    <Button size="sm" variant="ghost">Settings</Button>
  </CardFooter>
</Card>
```
