/** @jsxImportSource @opentui/solid */
/**
 * Voice activity in the TUI: a toast per heard command, one status line in the prompt
 * footer and a «Голос» block in the session sidebar (state and the last commands).
 * Read-only: it tails the same events file as the server plugin and never sends anything.
 */
import { Plugin } from "@opencode/plugin/tui"
import { join } from "node:path"
import { createSignal, For, Show } from "solid-js"
import { EMPTY_VIEW, STATE_LABEL, Tail, fold, stateDir } from "./lib.js"

const POLL_MS = 500
const time = (ts) => (/T(\d\d:\d\d)/.exec(ts) ?? [])[1] ?? ""

export default Plugin.define({
  id: "alice-mcp.voice-panel",
  setup(context) {
    const theme = context.theme
    const tail = new Tail(join(stateDir(), "voice-events.jsonl"))
    const [view, setView] = createSignal(fold(EMPTY_VIEW, tail.read())) // History: no toasts.

    const toast = (record) => {
      if (record.kind === "wake") {
        context.ui.toast.show({
          title: `Голос · ${record.station}`,
          message: record.command ? `«${record.command}»` : "«компьютер» — команды не услышал",
          variant: "info",
          duration: 4000,
        })
      } else if (record.kind === "state" && record.error) {
        context.ui.toast.show({ title: "Голос", message: String(record.error), variant: "warning" })
      } else if (record.kind === "state" && record.state === "listening") {
        context.ui.toast.show({ title: "Голос", message: `слушает «компьютер» · ${record.station}`, variant: "success" })
      }
    }

    const timer = setInterval(() => {
      const records = tail.read()
      if (!records.length) return
      setView((current) => fold(current, records))
      records.forEach(toast)
    }, POLL_MS)
    timer.unref?.()

    const tone = () => {
      const state = view().state
      return state === "listening"
        ? theme.text.feedback.success.base
        : state === "stopped" && view().error
          ? theme.text.feedback.warning.base
          : theme.text.muted
    }
    const label = () => STATE_LABEL[view().state] ?? view().state

    const unfooter = context.ui.slot({
      append: "prompt.footer.status",
      render: () => {
        const v = view()
        if (v.state === "unknown") return null
        const last = v.recent.at(-1)
        return (
          <text fg={tone()} wrapMode="none" truncate>
            🎙 {label()}
            {last?.command ? ` · «${last.command}»` : ""}
          </text>
        )
      },
    })

    const unsidebar = context.ui.slot({
      append: "sidebar.content",
      render: () => {
        const v = view()
        if (v.state === "unknown") return null
        return (
          <box flexDirection="column" flexShrink={0}>
            <text fg={theme.text.base}>
              <b>🎙 Голос</b>
            </text>
            <text fg={tone()} wrapMode="word">
              <span>
                {label()}
                {v.station ? ` · ${v.station}` : ""}
              </span>
            </text>
            <Show when={v.error}>
              <text fg={theme.text.feedback.warning.base} wrapMode="word">
                <span>{v.error}</span>
              </text>
            </Show>
            <For each={v.recent.slice(-3).reverse()}>
              {(item) => (
                <text fg={theme.text.muted} wrapMode="word">
                  <span>
                    {time(item.ts)} {item.command ? `«${item.command}»` : "— без команды"}
                  </span>
                </text>
              )}
            </For>
          </box>
        )
      },
    })

    return () => {
      clearInterval(timer)
      unfooter()
      unsidebar()
    }
  },
})
