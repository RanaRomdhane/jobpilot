import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { SessionGate } from "@/components/session-gate";
import { TaskToasts } from "@/components/task-toasts";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionGate>
      <TaskToasts />
      <AppShell>{children}</AppShell>
    </SessionGate>
  );
}
