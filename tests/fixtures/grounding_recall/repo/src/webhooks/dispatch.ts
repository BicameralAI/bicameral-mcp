// Inbound webhook event router (TypeScript runtime).
// Sibling of dispatch.py — same routing contract, different runtime.

export interface WebhookEvent {
  type: string;
  payload: unknown;
}

type Handler = (event: WebhookEvent) => Promise<void> | void;

export async function dispatchEvent(event: WebhookEvent): Promise<void> {
  // Route a verified webhook event to its subscriber handlers.
  // Looks up the event type → handler list mapping, fans out to each
  // handler with a per-handler retry policy. Errors in one handler do
  // not abort the rest. TS sibling of dispatch.py:dispatch_event.
  const handlers = subscribersFor(event.type);
  for (const handler of handlers) {
    try {
      await handler(event);
    } catch (exc) {
      recordHandlerFailure(event, handler, exc);
    }
  }
}

export function enqueueDispatch(event: WebhookEvent): void {
  // Queue an event for asynchronous dispatch — used when the inbound
  // request must respond immediately (Stripe 5s window).
  queue.push(event);
}

function subscribersFor(_eventType: string): Handler[] {
  throw new Error("not implemented");
}

function recordHandlerFailure(
  _event: WebhookEvent,
  _handler: Handler,
  _exc: unknown,
): void {
  throw new Error("not implemented");
}

const queue: WebhookEvent[] = [];
