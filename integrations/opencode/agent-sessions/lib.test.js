// Synthetic plugin context and private temporary state; no live client sessions.
import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { mkdtemp, mkdir, writeFile, chmod, rm, symlink } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import { registerTools, runBridge } from "./lib.js"

const repository = fileURLToPath(new URL("../../../", import.meta.url))
const python = "python3"
const script = join(repository, "scripts/agent_sessions.py")

async function fixture(t) {
  const project = await mkdtemp(join(tmpdir(), "agent-sessions-plugin-"))
  t.after(() => rm(project, { recursive: true, force: true }))
  return { project, python, script, stateDir: join(project, "runtime") }
}

function context(project) {
  const tools = new Map()
  const checks = []
  const ctx = {
    location: { directory: project },
    session: { get: async () => ({ location: { directory: project } }) },
    permission: { assert: async (request) => checks.push(request) },
    tool: { transform: async (transform) => {
      transform({ namespace() {}, add(tool) { tools.set(tool.name, tool) } })
    } },
  }
  return { ctx, tools, checks }
}

const invocation = () => ({ sessionID: "ses_synthetic", agent: "build", messageID: "msg_test",
  id: "tool_test", signal: new AbortController().signal })

test("registers three bounded native tools and checks permissions before dispatch", async (t) => {
  const paths = await fixture(t)
  const { ctx, tools, checks } = context(paths.project)
  const calls = []
  await registerTools(ctx, paths, async (...args) => { calls.push(args); return [] })
  assert.deepEqual([...tools.keys()], ["list", "send", "messages"])
  assert.equal(calls.length, 0) // Registration itself starts no subprocess or polling.
  await tools.get("list").execute({}, invocation())
  await tools.get("send").execute({ client: "codex", session_id: "thread_test", message: "Check tests" }, invocation())
  await tools.get("messages").execute({ client: "claude", session_id: "thread_test" }, invocation())
  assert.deepEqual(checks.map((check) => check.action),
    ["agent_sessions_list", "agent_sessions_send", "agent_sessions_messages"])
  assert.deepEqual(checks[1].resources, ["codex:thread_test"])
  assert.equal(checks[1].metadata, undefined) // Do not copy message text into permission metadata.
  assert.deepEqual(calls.map((call) => call[1]), ["list", "send", "messages"])
  for (const tool of tools.values()) assert.equal(tool.input.additionalProperties, false)
})

test("permission rejection propagates without invoking the bridge", async (t) => {
  const paths = await fixture(t)
  const { ctx, tools } = context(paths.project)
  const denied = new Error("synthetic rejection")
  ctx.permission.assert = async () => { throw denied }
  await registerTools(ctx, paths, () => assert.fail("must not execute"))
  await assert.rejects(tools.get("send").execute({ client: "codex", session_id: "test", message: "Hi" }, invocation()),
    (error) => error === denied)
})

test("session moved outside the project or through a symlink cannot access the bridge", async (t) => {
  const paths = await fixture(t)
  const { ctx, tools, checks } = context(paths.project)
  await registerTools(ctx, paths, () => assert.fail("must not execute"))
  ctx.session.get = async () => ({ location: { directory: dirname(paths.project) } })
  await assert.rejects(tools.get("list").execute({}, invocation()), /outside/)
  await symlink(dirname(paths.project), join(paths.project, "escape"))
  ctx.session.get = async () => ({ location: { directory: join(paths.project, "escape") } })
  await assert.rejects(tools.get("list").execute({}, invocation()), /outside/)
  assert.equal(checks.length, 0)
})

test("invalid clients, extra arguments and terminal controls never reach subprocess dispatch", async (t) => {
  const paths = await fixture(t)
  const { ctx, tools, checks } = context(paths.project)
  await registerTools(ctx, paths, () => assert.fail("must not execute"))
  for (const input of [
    { client: "shell", session_id: "test", message: "Hi" },
    { client: "codex", session_id: "--help", message: "Hi", executable: "/PRIVATE/not-allowed" },
    { client: "codex", session_id: "../other", message: "Hi" },
    { client: "codex", session_id: "test", message: "\u009b31m" },
    { client: "codex", session_id: "test", message: "я".repeat(1025) },
    { client: "codex", session_id: "test", message: "  " },
  ]) await assert.rejects(tools.get("send").execute(input, invocation()), /Invalid/)
  assert.equal(checks.length, 0)
})

test("native tools use the actual Python queue and return metadata-only receipts", async (t) => {
  const paths = await fixture(t)
  await mkdir(paths.stateDir, { mode: 0o700 })
  const payload = JSON.stringify({ cwd: paths.project, session_id: "synthetic-thread", hook_event_name: "SessionStart" })
  const hook = execFile(python, [script, "--project", paths.project, "--state-dir", paths.stateDir, "hook", "codex"])
  hook.stdin.end(payload)
  await new Promise((resolve, reject) => hook.once("error", reject).once("close", (code) => code === 0 ? resolve() : reject(new Error("hook failed"))))
  const { ctx, tools } = context(paths.project)
  await registerTools(ctx, paths)
  const listed = JSON.parse((await tools.get("list").execute({}, invocation())).content)
  assert.equal(listed[0].session_id, "synthetic-thread")
  const sent = JSON.parse((await tools.get("send").execute({ client: "codex", session_id: "synthetic-thread", message: "Synthetic test" }, invocation())).content)
  assert.equal(sent.status, "queued")
  const receipts = JSON.parse((await tools.get("messages").execute({ client: "codex", session_id: "synthetic-thread" }, invocation())).content)
  assert.equal(receipts[0].message_id, sent.message_id)
  assert.equal(receipts[0].status, "queued")
  assert.equal(receipts[0].body, undefined)
})

test("subprocess failures and cancellation never echo stderr or claim a safe send retry", async (t) => {
  const paths = await fixture(t)
  const failed = join(paths.project, "synthetic-python")
  await writeFile(failed, '#!/bin/sh\necho "SYNTHETIC_PRIVATE" >&2\nexit 1\n')
  await chmod(failed, 0o700)
  for (const operation of ["list", "send"]) {
    const input = operation === "send" ? { client: "codex", session_id: "test", message: "Hi" } : {}
    await assert.rejects(runBridge({ ...paths, python: failed }, operation, input), (error) => {
      assert.equal(String(error).includes("SYNTHETIC_PRIVATE"), false)
      if (operation === "send") assert.match(String(error), /unknown.*do not retry/i)
      return true
    })
  }
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(runBridge(paths, "send", { client: "codex", session_id: "test", message: "Hi" }, controller.signal), /do not retry/i)
})

test("entrypoint exports the V2 server plugin without SDK downloads", async () => {
  const { default: plugin } = await import("./index.js")
  assert.equal(plugin.id, "alice-mcp.agent-sessions")
  assert.equal(typeof plugin.setup, "function")
})
