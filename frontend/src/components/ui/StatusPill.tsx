import { Badge, type BadgeTone } from "./Badge";

/**
 * Domain status pills. Each state has its own colour AND its own glyph and
 * label, so meaning never depends on colour alone.
 */
const PILLS = {
  icp: {
    strong: { tone: "icp-strong", icon: "●●●", label: "ICP Strong" },
    medium: { tone: "icp-medium", icon: "●●○", label: "ICP Medium" },
    weak: { tone: "icp-weak", icon: "●○○", label: "ICP Weak" },
  },
  trigger: {
    yes: { tone: "trigger-yes", icon: "✓", label: "Trigger: Yes" },
    no: { tone: "trigger-no", icon: "–", label: "Trigger: No" },
  },
  ai: {
    add: { tone: "ai-add", icon: "+", label: "AI: ADD" },
    hold: { tone: "ai-hold", icon: "‖", label: "AI: HOLD" },
    skip: { tone: "ai-skip", icon: "×", label: "AI: SKIP" },
  },
} as const satisfies Record<
  string,
  Record<string, { tone: BadgeTone; icon: string; label: string }>
>;

type PillProps =
  | { kind: "icp"; value: keyof typeof PILLS.icp }
  | { kind: "trigger"; value: keyof typeof PILLS.trigger }
  | { kind: "ai"; value: keyof typeof PILLS.ai };

export function StatusPill({ kind, value }: PillProps) {
  const pills: Record<
    string,
    { tone: BadgeTone; icon: string; label: string }
  > = PILLS[kind];
  const pill = pills[value];
  if (!pill) return null; // unreachable for typed callers
  const { tone, icon, label } = pill;
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}
