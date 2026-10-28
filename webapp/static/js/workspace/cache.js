// Кэши деталей мойки и данных графика (LRU) с защитой от устаревших ответов:
// поколение растёт при каждой инвалидации, поздний ответ в кэш не попадает.

import { apiJson } from "../core/api.js";

export const detailCache = new Map();
const detailRequestCache = new Map();
const chartPayloadCache = new Map();
const chartPayloadRequestCache = new Map();
const DETAIL_CACHE_LIMIT = 200;
const CHART_PAYLOAD_CACHE_LIMIT = 80;
// Поколение данных рабочей области: растёт при каждой инвалидации кэшей
// (в том числе при фоновом автообновлении FTP). Ответ запроса, стартовавшего
// до инвалидации, в кэш уже не попадает — иначе устаревшие детали/график
// «воскресали» бы в только что очищенном кэше.
let workspaceDataGeneration = 0;

function setBoundedCacheEntry(cache, key, value, limit) {
  // Простая LRU-эвикция: не даём кэшам расти неограниченно между обновлениями данных.
  if (cache.has(key)) {
    cache.delete(key);
  }
  cache.set(key, value);
  while (cache.size > limit) {
    const oldestKey = cache.keys().next().value;
    cache.delete(oldestKey);
  }
}

// Кэшированный GET: готовый ответ из LRU-кэша, иначе общий запрос в полёте
// (повторные вызовы ждут тот же промис). Пока ответ летел, данные могли
// обновиться (фоновый FTP) и кэши — очиститься: устаревший ответ в новый кэш
// не кладём.
function cachedGet(cache, requests, limit, key, url) {
  if (cache.has(key)) {
    return Promise.resolve(cache.get(key));
  }
  if (requests.has(key)) {
    return requests.get(key);
  }

  const generation = workspaceDataGeneration;
  const request = apiJson(url)
    .then((payload) => {
      if (generation === workspaceDataGeneration) {
        setBoundedCacheEntry(cache, key, payload, limit);
      }
      return payload;
    })
    .finally(() => {
      // Только свой запрос: после инвалидации по этому ключу мог стартовать новый.
      if (requests.get(key) === request) {
        requests.delete(key);
      }
    });

  requests.set(key, request);
  return request;
}

export function getDetail(key) {
  const url = `/api/wash-details?key=${encodeURIComponent(key)}`;
  return cachedGet(detailCache, detailRequestCache, DETAIL_CACHE_LIMIT, key, url);
}

export function getChartPayload(url) {
  if (!url) {
    return Promise.reject(new Error("wash-chart-url-missing"));
  }
  return cachedGet(chartPayloadCache, chartPayloadRequestCache, CHART_PAYLOAD_CACHE_LIMIT, url, url);
}

export function prefetchChartPayload(url) {
  if (!url) {
    return;
  }
  void getChartPayload(url).catch(() => {});
}

export function prefetchWashContext(key) {
  if (!key) {
    return;
  }

  void getDetail(key)
    .then((detail) => {
      if (detail?.chart_data_url) {
        return getChartPayload(detail.chart_data_url);
      }
      return null;
    })
    .catch(() => {});
}

// Новое поколение и пустые кэши: ответы запросов, стартовавших до очистки,
// в кэш уже не попадут.
export function invalidateWashCaches() {
  workspaceDataGeneration += 1;
  detailCache.clear();
  detailRequestCache.clear();
  chartPayloadCache.clear();
  chartPayloadRequestCache.clear();
}
