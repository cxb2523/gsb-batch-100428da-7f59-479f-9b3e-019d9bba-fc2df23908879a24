import { isStopOperator, loadStopBoard } from "@/lib/stop-board"

export const dynamic = "force-dynamic"

export async function GET(request: Request) {
  const url = new URL(request.url)
  const op = url.searchParams.get("op")
  const id = url.searchParams.get("id")?.trim() ?? ""
  if (!isStopOperator(op) || !id) {
    return Response.json(
      { ok: false, error: "Stop missing", source: "missing" as const, fetchedAt: null },
      { status: 400 },
    )
  }
  try {
    const board = await loadStopBoard(op, id)
    if (!board.ok) {
      return Response.json(
        { ok: false, error: "Stop board failed", source: "missing" as const, fetchedAt: null },
        { status: 502 },
      )
    }
    return Response.json({
      ok: true,
      stop: board.stop,
      source: board.stop.source ?? "live",
      fetchedAt: board.stop.fetchedAt ?? null,
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : "Stop board failed"
    return Response.json(
      { ok: false, error: message, source: "missing" as const, fetchedAt: null },
      { status: 502 },
    )
  }
}
