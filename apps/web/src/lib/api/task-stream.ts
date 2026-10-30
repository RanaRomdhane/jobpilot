import type { TaskEvent } from "./client";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

/** Follow a background task over SSE until it finishes. Returns an unsubscribe function. */
export function followTask(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`/api/v1/tasks/${encodeURIComponent(taskId)}/events`);
  source.addEventListener("task", (message) => {
    const event = JSON.parse((message as MessageEvent<string>).data) as TaskEvent;
    onEvent(event);
    if (TERMINAL.has(event.status)) source.close();
  });
  source.onerror = () => {
    source.close();
    onError?.();
  };
  return () => source.close();
}
