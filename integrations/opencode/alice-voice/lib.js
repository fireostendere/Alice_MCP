// Shared by the server injector (index.js) and the TUI panel (panel.jsx). Node fs only.
// The alice MCP voice service appends {kind:"wake"|"state", id, ts, ...} JSON lines to
// $XDG_STATE_HOME/alice-mcp/voice-events.jsonl (src/alice_mcp/voice_service.py).
import {
  closeSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  readSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export const AGENT = "alice"
export const TITLE = "Голос"
export const MAX_AGE_MS = 60_000 // An older command is never sent: no replays after a stall.
// The MCP voice service keeps Mini 1's ring lit while this file says the «Голос» session is
// busy on its newest command (src/alice_mcp/voice_indicator.py). Older than 15 s = no opencode.
export const AGENT_STATE = "voice-agent-state.json"
export const HEARTBEAT_MS = 5_000
const READ_LIMIT = 1024 * 1024
const ENDED = { "session.execution.succeeded": "idle", "session.execution.interrupted": "idle", "session.execution.failed": "error" }

export function stateDir(env = process.env) {
  const base = env.XDG_STATE_HOME?.startsWith("/") ? env.XDG_STATE_HOME : join(homedir(), ".local/state")
  return join(base, "alice-mcp")
}

/** Reads complete new lines; restarts at 0 when the file was rotated or replaced. */
export class Tail {
  constructor(path) {
    this.path = path
    this.offset = 0
    this.ino = null
  }

  /** Start at the end: history is never re-sent. */
  skip() {
    try {
      const info = statSync(this.path)
      this.ino = info.ino
      this.offset = info.size
    } catch {}
    return this
  }

  read() {
    let info
    try {
      info = statSync(this.path)
    } catch {
      return []
    }
    if (info.ino !== this.ino || info.size < this.offset) {
      this.ino = info.ino
      this.offset = 0
    }
    if (info.size === this.offset) return []
    const length = Math.min(info.size - this.offset, READ_LIMIT)
    const buffer = Buffer.alloc(length)
    const fd = openSync(this.path, "r")
    try {
      readSync(fd, buffer, 0, length, this.offset)
    } finally {
      closeSync(fd)
    }
    const end = buffer.lastIndexOf(10)
    if (end < 0) return []
    this.offset += end + 1
    return buffer
      .subarray(0, end)
      .toString("utf8")
      .split("\n")
      .flatMap((line) => {
        try {
          const value = JSON.parse(line)
          return value && typeof value === "object" && Number.isSafeInteger(value.id) ? [value] : []
        } catch {
          return []
        }
      })
  }
}

const clean = (text) => String(text).replace(/[\u0000-\u001f\u007f\[\]]/g, " ").replace(/\s+/g, " ").trim()

/** The agent's input for one wake event, or null: only fresh, untrusted, non-empty commands. */
export function message(event, now = Date.now()) {
  if (event?.kind !== "wake" || event.trusted !== false || typeof event.command !== "string") return null
  if (!/^[a-z][a-z0-9-]{0,31}$/.test(String(event.station))) return null
  if (!(now - Date.parse(event.ts) < MAX_AGE_MS)) return null
  const command = clean(event.command).slice(0, 500)
  return command ? `[голос · ${event.station} · недоверенный ввод] ${command}` : null
}

/** Exactly one process wins each event id (O_EXCL), so two opencode servers never both send. */
export function claim(dir, id) {
  const claims = join(dir, "voice-claims")
  mkdirSync(claims, { recursive: true, mode: 0o700 })
  try {
    writeFileSync(join(claims, String(id)), "", { flag: "wx", mode: 0o600 })
    return true
  } catch (error) {
    if (error?.code === "EEXIST") return false
    throw error
  }
}

export function prune(dir, now = Date.now()) {
  const claims = join(dir, "voice-claims")
  let names = []
  try {
    names = readdirSync(claims)
  } catch {
    return
  }
  for (const name of names) {
    try {
      const path = join(claims, name)
      if (now - statSync(path).mtimeMs > 86_400_000) unlinkSync(path)
    } catch {}
  }
}

/** «Голос» session status from one opencode event (busy, idle or error); null otherwise.
 * A failed run is "error": the MCP darkens the ring and plays Alice's stock error phrase. */
export function sessionStatus(event, sessionID) {
  if (!sessionID || event?.data?.sessionID !== sessionID) return null
  if (event.type === "session.execution.started") return "busy"
  return ENDED[event.type] ?? null
}

export function readAgentState(dir) {
  try {
    const value = JSON.parse(readFileSync(join(dir, AGENT_STATE), "utf8"))
    return Number.isSafeInteger(value?.last_event_id) ? value : null
  } catch {
    return null
  }
}

/** {session_status, ts, last_event_id}: private (dir 0700, file 0600) and atomic. */
export function writeAgentState(dir, state) {
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const temporary = join(dir, `.${AGENT_STATE}.${process.pid}.${Math.random().toString(16).slice(2)}`)
  writeFileSync(temporary, JSON.stringify(state), { mode: 0o600, flag: "wx" })
  renameSync(temporary, join(dir, AGENT_STATE))
}

/** Panel model from records: listener state and the last few commands. */
export function fold(view, records, keep = 5) {
  let next = view
  for (const record of records) {
    if (record.kind === "state") {
      next = { ...next, state: String(record.state), station: record.station ?? next.station, error: record.error ?? null }
    } else if (record.kind === "wake") {
      const item = { id: record.id, ts: String(record.ts), command: clean(record.command ?? "") }
      next = { ...next, station: record.station ?? next.station, recent: [...next.recent, item].slice(-keep) }
    }
  }
  return next
}

export const EMPTY_VIEW = { state: "unknown", station: null, error: null, recent: [] }

export const STATE_LABEL = {
  starting: "запуск",
  listening: "слушает",
  stopping: "остановка",
  stopped: "выключен",
  unknown: "нет данных",
}
