import {
  applyTheme,
  initTheme,
  readPersistedTheme,
  resolveTheme,
  setTheme,
  toggleTheme,
  writePersistedTheme,
} from "@/lib/theme";

const STORAGE_KEY = "openvdi.theme";

describe("theme module", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    vi.restoreAllMocks();
  });

  it("readPersistedTheme returns null when storage is empty", () => {
    expect(readPersistedTheme()).toBeNull();
  });

  it("readPersistedTheme returns the persisted value", () => {
    window.localStorage.setItem(STORAGE_KEY, "dark");
    expect(readPersistedTheme()).toBe("dark");
  });

  it("readPersistedTheme returns null on garbage", () => {
    window.localStorage.setItem(STORAGE_KEY, "lavender");
    expect(readPersistedTheme()).toBeNull();
  });

  it("applyTheme sets and removes the attribute", () => {
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("resolveTheme prefers storage over system", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true, // system says dark
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList);

    expect(resolveTheme()).toBe("dark"); // no storage → system

    writePersistedTheme("light");
    expect(resolveTheme()).toBe("light"); // storage wins
  });

  it("initTheme applies the resolved theme to the DOM", () => {
    writePersistedTheme("dark");
    initTheme();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    writePersistedTheme("light");
    initTheme();
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("toggleTheme flips and persists", () => {
    writePersistedTheme("light");
    expect(toggleTheme()).toBe("dark");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    expect(toggleTheme()).toBe("light");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("setTheme('system') clears storage and resolves to system", () => {
    writePersistedTheme("dark");
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList);

    expect(setTheme("system")).toBe("light");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
