import { expectTypeOf } from "vitest";

import type {
  ConsoleTicketRead,
  NoVNCTicketRead,
  SpiceTicketRead,
} from "@/types";

// describe/it/expect come from vitest globals.

describe("ConsoleTicketRead narrowing", () => {
  it("narrows to NoVNCTicketRead on kind === 'novnc'", () => {
    const ticket = {} as ConsoleTicketRead;
    if (ticket.kind === "novnc") {
      expectTypeOf(ticket).toEqualTypeOf<NoVNCTicketRead>();
      expectTypeOf(ticket.websocket_url).toEqualTypeOf<string>();
      expectTypeOf(ticket.cert_pem).toEqualTypeOf<string | null>();
    }
  });

  it("narrows to SpiceTicketRead on kind === 'spice'", () => {
    const ticket = {} as ConsoleTicketRead;
    if (ticket.kind === "spice") {
      expectTypeOf(ticket).toEqualTypeOf<SpiceTicketRead>();
      expectTypeOf(ticket.tls_port).toEqualTypeOf<number | null>();
    }
  });

  it("forces exhaustive switch handling", () => {
    function rendererFor(t: ConsoleTicketRead): string {
      switch (t.kind) {
        case "novnc":
          return "novnc";
        case "spice":
          return "spice";
        case "webmks":
          return "webmks";
        case "rdp":
          return "rdp";
      }
      // The `never`-narrowing here is what makes adding a new kind to
      // ConsoleTicketRead a compile error in this test file, forcing
      // M3-06's renderer (and any future renderer) to handle it.
      const _exhaustive: never = t;
      return _exhaustive;
    }
    expectTypeOf(rendererFor).toBeFunction();
  });
});
