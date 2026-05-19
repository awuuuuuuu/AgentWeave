import { NextRequest, NextResponse } from "next/server"

/**
 * 高德驾车路线规划代理（服务端调用，绕过 CORS）
 * GET /api/amap-route?origin=lng,lat&destination=lng,lat
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl
  const origin = searchParams.get("origin")
  const destination = searchParams.get("destination")
  // Web服务 Key（在高德控制台单独创建，平台类型选"Web服务"）
  const key = process.env.AMAP_SERVICE_KEY ?? process.env.NEXT_PUBLIC_AMAP_KEY

  if (!key) {
    return NextResponse.json({ error: "AMAP_API_KEY not configured" }, { status: 500 })
  }
  if (!origin || !destination) {
    return NextResponse.json({ error: "origin and destination required" }, { status: 400 })
  }

  const url = `https://restapi.amap.com/v3/direction/driving?key=${key}&origin=${origin}&destination=${destination}&output=json`

  const resp = await fetch(url, { cache: "no-store" })
  const data = await resp.json()
  return NextResponse.json(data)
}
