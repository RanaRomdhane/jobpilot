import type { TaskEvent } from "./client";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

/** Tasks a page is following right now: it reports their outcome itself, so the global
 *  toasts stay quiet for them (no duplicate, untranslated message). */
const followed = new Set<string>();

export function isFollowed(taskId: string): boolean {
  return followed.has(taskId);
}

/** Follow a background task over SSE until it finishes. Returns an unsubscribe function. */
export function followTask(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onError?: () => void,
): () => void {
  followed.add(taskId);
  const source = new EventSource(`/api/v1/tasks/${encodeURIComponent(taskId)}/events`);
  // forget the task a little after it ends: the global stream may deliver its event later
  const release = () => setTimeout(() => followed.delete(taskId), 30_000);
  source.addEventListener("task", (message) => {
    const event = JSON.parse((message as MessageEvent<string>).data) as TaskEvent;
    onEvent(event);
    if (TERMINAL.has(event.status)) {
      source.close();
      release();
    }
  });
  source.onerror = () => {
    source.close();
    release();
    onError?.();
  };
  return () => {
    source.close();
    release();
  };
}
