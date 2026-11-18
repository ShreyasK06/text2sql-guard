import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for insecure origins or a denied clipboard permission.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export function CopyButton({ text, label = "Copy SQL" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function onClick() {
    setState((await copyText(text)) ? "ok" : "fail");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1800);
  }
  return (
    <>
      <button type="button" className="btn ghost sm" onClick={onClick}>
        <Icon name={state === "ok" ? "check" : "copy"} />
        {state === "ok" ? "Copied" : state === "fail" ? "Copy failed" : label}
      </button>
      <span className="sr-only" role="status">{state === "ok" ? "Copied to the clipboard" : state === "fail" ? "Could not copy" : ""}</span>
    </>
  );
}
