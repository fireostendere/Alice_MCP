// Three fixed bridge operations, never a model-supplied executable, path or shell command.
import { execFile } from "node:child_process"
import { realpath } from "node:fs/promises"
import { isAbsolute, relative } from "node:path"

const descriptions = {
  list: "List hook-connected Codex/Claude Code sessions in this project. Last events are not proof of process liveness.",
  send: "Queue one operator-requested message for an exact connected session. Does not wake idle sessions or grant permissions. Never automatically retry an unknown outcome.",
  messages: "Inspect metadata-only message receipts. Claimed does not prove receipt, reading, acknowledgment or execution.",
}
const identity = { client: { type: "string", enum: ["codex", "claude"] },
  session_id: { type: "string", pattern: "^[A-Za-z0-9_-]{1,128}$" } }

function argumentsFor(operation, input) {
  const keys = operation === "list" ? [] : operation === "messages" ? ["client", "session_id"] : ["client", "session_id", "message"]
  if (!Object.hasOwn(descriptions, operation) || !input || typeof input !== "object" || Array.isArray(input)
      || Object.keys(input).length !== keys.length || keys.some((key) => !Object.hasOwn(input, key))) {
    throw new Error("Invalid agent session operation or arguments")
  }
  if (operation === "list") return ["list"]
  if (!["codex", "claude"].includes(input.client) || typeof input.session_id !== "string"
      || !/^[A-Za-z0-9_-]{1,128}$/.test(input.session_id)) throw new Error("Invalid client or session ID")
  if (operation === "send" && (typeof input.message !== "string" || !input.message.trim()
      || Buffer.byteLength(input.message, "utf8") > 2048 || /\p{Cs}/u.test(input.message)
      || /\p{Cc}/u.test(input.message.replace(/[\n\t]/g, "")))) throw new Error("Invalid message")
  return [operation, input.client, "--", input.session_id]
}

function inside(project, directory) {
  const path = relative(project, directory)
  return path !== ".." && !path.startsWith("../") && !isAbsolute(path)
}

async function assertProject(project, directory) {
  let actual
  try {
    actual = await realpath(directory)
  } catch {
    throw new Error("Agent session project location cannot be verified")
  }
  if (!inside(project, actual)) throw new Error("Session is outside this plugin's project")
}

export function runBridge(paths, operation, input, signal) {
  const args = argumentsFor(operation, input)
  const failure = () => new Error(operation === "send"
    ? "Message outcome is unknown; do not retry automatically. Inspect local receipts first."
    : "Agent session bridge unavailable; inspect the configured interpreter and private state locally.")
  return new Promise((resolve, reject) => {
    try {
      const child = execFile(paths.python, [paths.script, "--project", paths.project,
        "--state-dir", paths.stateDir, ...args], {
        cwd: paths.project, signal, timeout: 5000,
        // ponytail: bounded snapshot; paginate if metadata exceeds 256 KiB.
        maxBuffer: 256 * 1024,
      }, (error, stdout) => {
        if (error) return reject(failure()) // Never expose raw stderr or exception text.
        try {
          const result = JSON.parse(stdout)
          if (operation === "send" ? result?.status !== "queued" : !Array.isArray(result)) throw failure()
          resolve(result)
        } catch {
          reject(failure())
        }
      })
      child.stdin.on("error", () => {}) // EPIPE is reported through the bounded process result.
      child.stdin.end(operation === "send" ? input.message : "")
    } catch {
      reject(failure())
    }
  })
}

export async function registerTools(ctx, paths, invoke = runBridge) {
  const project = await realpath(paths.project)
  await assertProject(project, ctx.location?.directory)
  await ctx.tool.transform((editor) => {
    editor.namespace({ name: "agent_sessions", description: "Project-local Codex and Claude Code hook sessions" })
    for (const operation of Object.keys(descriptions)) {
      const properties = operation === "list" ? {} : operation === "send"
        ? { ...identity, message: { type: "string", minLength: 1, maxLength: 2048 } } : identity
      editor.add({
        name: operation,
        description: descriptions[operation],
        input: { type: "object", properties, required: Object.keys(properties), additionalProperties: false },
        options: { namespace: "agent_sessions", codemode: true },
        async execute(input, context) {
          argumentsFor(operation, input)
          const session = await ctx.session.get({ sessionID: context.sessionID })
          await assertProject(project, session?.location?.directory)
          await ctx.permission.assert({
            sessionID: context.sessionID, agent: context.agent,
            source: { type: "tool", messageID: context.messageID, id: context.id },
            action: `agent_sessions_${operation}`,
            resources: operation === "list" ? [project] : [`${input.client}:${input.session_id}`],
          })
          context.signal?.throwIfAborted()
          return { content: JSON.stringify(await invoke(paths, operation, input, context.signal)) }
        },
      })
    }
  })
}
