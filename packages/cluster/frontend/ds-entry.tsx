// Design-system bundle entry for /design-sync (claude.ai/design).
// Re-exports ONLY the reusable UI primitives under src/components/ui/ — the
// app screens (ChatArea, Admin*, Room*, …) depend on routing/websocket/API
// context and are intentionally excluded from the synced surface.
// `@/` aliases resolve via cfg.tsconfig (tsconfig.json → "@/*": ["./src/*"]).
export * from "@/components/ui/avatar";
export * from "@/components/ui/badge";
export * from "@/components/ui/button";
export * from "@/components/ui/card";
export * from "@/components/ui/dialog";
export * from "@/components/ui/input";
export * from "@/components/ui/label";
export * from "@/components/ui/scroll-area";
export * from "@/components/ui/separator";
export * from "@/components/ui/table";
export * from "@/components/ui/tabs";
export * from "@/components/ui/chat/index";
