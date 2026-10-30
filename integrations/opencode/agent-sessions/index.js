// OpenCode V2 server plugin. Nothing is spawned or polled during registration.
import { realpath } from "node:fs/promises"
import { homedir } from "node:os"
import { isAbsolute, join } from "node:path"
import { fileURLToPath } from "node:url"
import { registerTools } from "./lib.js"

export default {
  id: "alice-mcp.agent-sessions",
  async setup(ctx) {
    if (process.platform === "win32") throw new Error("Agent sessions require Linux/WSL")
    const project = await realpath(fileURLToPath(new URL("../../../", import.meta.url)))
    const python = ctx.options?.python ?? join(project, ".venv/bin/python")
    const stateDir = ctx.options?.stateDir ?? join(homedir(), ".alice-agent-sessions")
    if (![python, stateDir].every((path) => typeof path === "string" && isAbsolute(path))) {
      throw new Error("Agent sessions require absolute operator-configured paths")
    }
    await registerTools(ctx, { project, python, stateDir, script: join(project, "scripts/agent_sessions.py") })
  },
}
