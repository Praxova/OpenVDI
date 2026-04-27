import { act, render } from "@testing-library/react";
import { StrictMode, createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  NoVNCViewer,
  type NoVNCViewerHandle,
} from "@/components/NoVNCViewer";
import type { NoVNCTicketRead } from "@/types";

// ── Mock @novnc/novnc ─────────────────────────────────────────

/**
 * Single shared RFB-mock surface. The mocked class records its
 * constructor invocations into `rfbConstructions` and exposes the
 * latest instance via `latestRFB` for event-driven assertions.
 *
 * The mock implements EventTarget for real — addEventListener /
 * removeEventListener / dispatchEvent work the way the production
 * RFB class does. This means tests can dispatch real CustomEvents
 * and rely on noVNC-shaped event dispatch.
 */

interface MockRFB extends EventTarget {
  scaleViewport: boolean;
  resizeSession: boolean;
  disconnect: ReturnType<typeof vi.fn>;
  sendCtrlAltDel: ReturnType<typeof vi.fn>;
  __target: HTMLElement;
  __url: string;
  __options: unknown;
}

const rfbConstructions: MockRFB[] = [];

function makeMockRFB(
  target: HTMLElement,
  url: string,
  options: unknown,
): MockRFB {
  const instance = new EventTarget() as MockRFB;
  instance.scaleViewport = false;
  instance.resizeSession = false;
  instance.disconnect = vi.fn();
  instance.sendCtrlAltDel = vi.fn();
  instance.__target = target;
  instance.__url = url;
  instance.__options = options;
  // Inject a child to simulate noVNC's canvas creation. The viewer's
  // container.replaceChildren() should then strip it on cleanup.
  const fakeCanvas = document.createElement("canvas");
  fakeCanvas.dataset.testid = "fake-noVNC-canvas";
  target.appendChild(fakeCanvas);
  rfbConstructions.push(instance);
  return instance;
}

vi.mock("@novnc/novnc", () => {
  return {
    default: class MockRFBClass {
      constructor(target: HTMLElement, url: string, options: unknown) {
        // Replace `this` with the EventTarget-backed mock so callers
        // get real addEventListener semantics. Object.assign couldn't
        // copy from EventTarget (browser builtin), so we return the
        // mock directly — JS classes allow this when the constructor
        // returns an object.
        return makeMockRFB(target, url, options);
      }
    },
  };
});

function latestRFB(): MockRFB {
  const last = rfbConstructions.at(-1);
  if (last === undefined) throw new Error("no RFB constructions yet");
  return last;
}

// ── Test setup ────────────────────────────────────────────────

const SAMPLE_TICKET: NoVNCTicketRead = {
  kind: "novnc",
  websocket_url:
    "wss://pve1.example.com:5900/api2/json/nodes/pve1/qemu/100/vncwebsocket?port=5900&vncticket=abc",
  password: "test-password",
  cert_pem: null,
};

function makeTicket(suffix = ""): NoVNCTicketRead {
  return {
    ...SAMPLE_TICKET,
    websocket_url: SAMPLE_TICKET.websocket_url + suffix,
  };
}

beforeEach(() => {
  rfbConstructions.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Specs ─────────────────────────────────────────────────────

describe("NoVNCViewer", () => {
  it("constructs RFB with the ticket URL, password, and protocols", () => {
    render(<NoVNCViewer ticket={SAMPLE_TICKET} />);
    expect(rfbConstructions).toHaveLength(1);
    const rfb = latestRFB();
    expect(rfb.__url).toBe(SAMPLE_TICKET.websocket_url);
    expect(rfb.__options).toMatchObject({
      credentials: { password: SAMPLE_TICKET.password },
      wsProtocols: ["binary"],
      shared: true,
    });
  });

  it("sets scaleViewport=true and resizeSession=false after construction", () => {
    render(<NoVNCViewer ticket={SAMPLE_TICKET} />);
    const rfb = latestRFB();
    expect(rfb.scaleViewport).toBe(true);
    expect(rfb.resizeSession).toBe(false);
  });

  it("fires onConnect when the RFB connect event fires", () => {
    const onConnect = vi.fn();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} onConnect={onConnect} />);
    act(() => {
      latestRFB().dispatchEvent(new Event("connect"));
    });
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it("fires onDisconnect with clean=true on a clean disconnect", () => {
    const onDisconnect = vi.fn();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} onDisconnect={onDisconnect} />);
    act(() => {
      latestRFB().dispatchEvent(
        new CustomEvent("disconnect", { detail: { clean: true } }),
      );
    });
    expect(onDisconnect).toHaveBeenCalledWith(true);
  });

  it("fires onDisconnect with clean=false on an unclean disconnect", () => {
    const onDisconnect = vi.fn();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} onDisconnect={onDisconnect} />);
    act(() => {
      latestRFB().dispatchEvent(
        new CustomEvent("disconnect", { detail: { clean: false } }),
      );
    });
    expect(onDisconnect).toHaveBeenCalledWith(false);
  });

  it("fires onError with reason on securityfailure", () => {
    const onError = vi.fn();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} onError={onError} />);
    act(() => {
      latestRFB().dispatchEvent(
        new CustomEvent("securityfailure", {
          detail: { status: 1, reason: "Authentication failed" },
        }),
      );
    });
    expect(onError).toHaveBeenCalledWith("Authentication failed");
  });

  it("fires onError on credentialsrequired", () => {
    const onError = vi.fn();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} onError={onError} />);
    act(() => {
      latestRFB().dispatchEvent(
        new CustomEvent("credentialsrequired", {
          detail: { types: ["password"] },
        }),
      );
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![0]).toContain("credentials");
  });

  it("imperative handle's sendCtrlAltDel calls the RFB instance", () => {
    const ref = createRef<NoVNCViewerHandle>();
    render(<NoVNCViewer ticket={SAMPLE_TICKET} ref={ref} />);
    expect(ref.current).not.toBeNull();
    act(() => {
      ref.current!.sendCtrlAltDel();
    });
    expect(latestRFB().sendCtrlAltDel).toHaveBeenCalledTimes(1);
  });

  it("sendCtrlAltDel is a no-op when called after unmount", () => {
    const ref = createRef<NoVNCViewerHandle>();
    const { unmount } = render(<NoVNCViewer ticket={SAMPLE_TICKET} ref={ref} />);
    const initialRFB = latestRFB();
    unmount();
    // After unmount, the ref is still attached but rfbRef.current is null.
    // The handle returns undefined and does not throw.
    expect(() => ref.current?.sendCtrlAltDel()).not.toThrow();
    // No call against the original RFB happened post-unmount.
    expect(initialRFB.sendCtrlAltDel).not.toHaveBeenCalled();
  });

  it("calls rfb.disconnect() and clears container on unmount", () => {
    const { unmount, container } = render(
      <NoVNCViewer ticket={SAMPLE_TICKET} />,
    );
    const rfb = latestRFB();
    // The mock added a fake canvas during construction; verify it's there.
    expect(
      container.querySelector('[data-testid="fake-noVNC-canvas"]'),
    ).not.toBeNull();
    unmount();
    expect(rfb.disconnect).toHaveBeenCalledTimes(1);
    // After unmount, the React tree is gone, so the container query is
    // unreliable. We assert via the rfb's __target reference instead.
    expect(rfb.__target.children.length).toBe(0);
  });

  it("disconnects the old RFB and constructs a new one when the ticket reference changes", () => {
    const t1 = makeTicket("&t=1");
    const t2 = makeTicket("&t=2");
    const { rerender } = render(<NoVNCViewer ticket={t1} />);
    expect(rfbConstructions).toHaveLength(1);
    const firstRFB = latestRFB();

    rerender(<NoVNCViewer ticket={t2} />);
    expect(firstRFB.disconnect).toHaveBeenCalledTimes(1);
    expect(rfbConstructions).toHaveLength(2);
    expect(latestRFB().__url).toBe(t2.websocket_url);
  });

  it("does NOT re-run the effect when only callback identities change", () => {
    const onConnect1 = vi.fn();
    const onConnect2 = vi.fn();
    const { rerender } = render(
      <NoVNCViewer ticket={SAMPLE_TICKET} onConnect={onConnect1} />,
    );
    expect(rfbConstructions).toHaveLength(1);

    rerender(<NoVNCViewer ticket={SAMPLE_TICKET} onConnect={onConnect2} />);
    // Same ticket reference — no reconstruction.
    expect(rfbConstructions).toHaveLength(1);

    // The new callback is the one that fires.
    act(() => {
      latestRFB().dispatchEvent(new Event("connect"));
    });
    expect(onConnect1).not.toHaveBeenCalled();
    expect(onConnect2).toHaveBeenCalledTimes(1);
  });

  it("StrictMode double-mount: 2 constructs, 1 cleanup before second mount, 2 disconnects total", () => {
    // StrictMode in dev fires effect → cleanup → effect again before
    // the next render commit. We verify the construct/disconnect
    // counts match exactly: any drift would indicate a stacking bug.
    const { unmount } = render(
      <StrictMode>
        <NoVNCViewer ticket={SAMPLE_TICKET} />
      </StrictMode>,
    );

    // Two constructions across the double-mount.
    expect(rfbConstructions).toHaveLength(2);

    // The first construction's cleanup ran before the second
    // construction (otherwise we'd have 2 stacked canvases).
    expect(rfbConstructions[0]!.disconnect).toHaveBeenCalledTimes(1);
    // The second construction is still live.
    expect(rfbConstructions[1]!.disconnect).not.toHaveBeenCalled();

    unmount();

    // Both eventual cleanups fire.
    expect(rfbConstructions[0]!.disconnect).toHaveBeenCalledTimes(1);
    expect(rfbConstructions[1]!.disconnect).toHaveBeenCalledTimes(1);
  });
});
