/**
 * Profile API client — wraps the /api/v1/profile endpoints (P1-20).
 *
 * P1-22's ProfileView uses these to load knowledge-point accuracy
 * stats and feed them into a radar chart.
 */

import { http } from './http'

export interface KnowledgeStat {
  tag: string
  total: number
  passed: number
  accuracy: number
}

export interface KnowledgeStatsResponse {
  stats: KnowledgeStat[]
}

export interface RadarIndicator {
  name: string
  max: number
}

export interface RadarResponse {
  indicators: RadarIndicator[]
  values: number[]
  tags: string[]
}

/**
 * Fetch per-tag accuracy stats.
 *
 * @param tags Optional filter — when provided, returns placeholders
 *   (total=0, accuracy=0) for tags with no submissions, so the radar
 *   chart axis stays stable.
 */
export async function getKnowledgeStats(
  tags?: string[],
): Promise<KnowledgeStat[]> {
  const params = tags ? { tags } : undefined
  const res = await http.get<KnowledgeStatsResponse>(
    '/api/v1/profile/knowledge-stats',
    { params },
  )
  return res.data.stats
}

/**
 * Fetch ECharts-ready radar payload.
 *
 * Returns three parallel arrays (indicators, values, tags) that can
 * be fed directly into a ``<v-chart>`` instance.
 */
export async function getRadar(tags?: string[]): Promise<RadarResponse> {
  const params = tags ? { tags } : undefined
  const res = await http.get<RadarResponse>('/api/v1/profile/radar', { params })
  return res.data
}
