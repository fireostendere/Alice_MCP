// Server plugin: voice commands from the alice MCP go to the `alice` agent.
// Each fresh, non-empty wake event becomes one queued user message in a dedicated session
// titled "Голос" (agent alice, this project), created on first use and reused after.
// A dedicated session keeps voice commands out of the owner's typed conversations and
// works with no TUI open. The text is tagged as untrusted spoken input.
// The server that sent the newest command reports the session busy/idle/error status (from its
// execution events) to voice-agent-state.json, so the MCP lights Mini 1's ring meanwhile.
import { join } from "node:path"
import {
  AGENT,
  HEARTBEAT_MS,
  TITLE,
  Tail,
  claim,
  message,
  prune,
  readAgentState,
  sessionStatus,
  stateDir,
  writeAgentState,
} from "./lib.js"

const POLL_MS = 500

export default {
  id: "alice-mcp.voice",
  setup(ctx) {
    const dir = stateDir()
    const tail = new Tail(join(dir, "voice-events.jsonl")).skip()
    const directory = ctx.location?.directory
    let sessionID = null
    let busy = false
    let status = "idle"
    let sentID = 0
    prune(dir)

    function publish() {
      if (!sentID) return // Only a server that sent a command reports on it.
      if ((readAgentState(dir)?.last_event_id ?? 0) > sentID) return // Another server sent a newer one.
      try {
        writeAgentState(dir, { session_status: status, ts: Date.now(), last_event_id: sentID })
      } catch (error) {
        console.error("alice-voice: could not write the agent state:", error?.message ?? error)
      }
    }

    const events = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: events.signal })) {
          const next = sessionStatus(event, sessionID)
          if (!next) continue
          status = next
          publish()
        }
      } catch (error) {
        if (!events.signal.aborted) console.error("alice-voice: no session events:", error?.message ?? error)
      }
    })()

    const unwrap = (value) => value?.data ?? value
    const usable = (s) => s && s.agent === AGENT && s.title === TITLE && !s.parentID && !s.time?.archived

    async function voiceSession() {
      if (sessionID) {
        const known = await ctx.session.get({ sessionID }).then(unwrap, () => null)
        if (usable(known)) return sessionID
      }
      const listed = unwrap(await ctx.session.list({ search: TITLE, order: "desc", ...(directory ? { directory } : {}) }))
      const found = (Array.isArray(listed) ? listed : []).find(usable)
      sessionID = found?.id ?? unwrap(await ctx.session.create({ title: TITLE, agent: AGENT })).id
      return sessionID
    }

    async function tick() {
      if (busy) return
      busy = true
      try {
        for (const event of tail.read()) {
          const text = message(event)
          if (!text || !claim(dir, event.id)) continue
          await ctx.session.prompt({
            sessionID: await voiceSession(),
            text,
            delivery: "queue", // One command after another, never steering a running one.
            metadata: { source: "alice-voice", eventID: event.id, station: event.station },
          })
          // Queued now; its run's end arrives later (a run takes seconds, the call does not).
          sentID = event.id
          status = "busy"
          publish()
        }
      } catch (error) {
        console.error("alice-voice: could not hand a voice command to the agent:", error?.message ?? error)
      } finally {
        busy = false
      }
    }

    const timer = setInterval(() => void tick(), POLL_MS)
    const beat = setInterval(publish, HEARTBEAT_MS)
    timer.unref?.()
    beat.unref?.()
    return () => {
      clearInterval(timer)
      clearInterval(beat)
      events.abort()
    }
  },
}
