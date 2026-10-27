import {loadMatches} from './events.js';

function checkCancelled(signal) {
  if (signal?.aborted) throw new DOMException('Catalogue refresh cancelled.', 'AbortError');
}

function safeMessage(error) {
  return String(error?.message || error || 'service unavailable')
    .replace(/https?:\/\/[^\s"'<>]+/gi, '[URL]')
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[redacted]')
    .replace(/[\x00-\x1f\x7f]/g, ' ')
    .slice(0, 400);
}

function watchIdentity(url) {
  if (typeof url !== 'string' || !url.trim()) return null;
  try {
    const parsed = new URL(url);
    return parsed.origin + parsed.pathname.replace(/\/$/, '');
  } catch {
    return url.trim();
  }
}

function uniqueMatches(matches) {
  const ids = new Set();
  const watchUrls = new Set();
  return matches.filter(match => {
    if (!match || typeof match !== 'object') return false;
    const id = match.id == null ? null : String(match.id);
    const watchUrl = watchIdentity(match.hotstarUrl);
    if ((id && ids.has(id)) || (watchUrl && watchUrls.has(watchUrl))) return false;
    if (id) ids.add(id);
    if (watchUrl) watchUrls.add(watchUrl);
    return true;
  }).sort((a, b) => Number(Boolean(b.isLive)) - Number(Boolean(a.isLive))
    || (Number(a.startTimeMs) || 0) - (Number(b.startTimeMs) || 0));
}

/** Combine independent community feeds and the signed-in Hotstar live catalogue. */
export async function loadCatalogue(backend, {signal, refresh = false, fetchImpl = fetch} = {}) {
  checkCancelled(signal);
  const [community, hotstar] = await Promise.allSettled([
    loadMatches(fetchImpl, signal),
    Promise.resolve().then(() => backend.request(`/api/hotstar/live${refresh ? '?refresh=1' : ''}`, undefined, 60000)),
  ]);
  checkCancelled(signal);

  const warnings = [];
  const matches = [];
  let partial = false;
  if (community.status === 'fulfilled') {
    matches.push(...community.value.matches);
    warnings.push(...community.value.warnings);
    partial ||= community.value.warnings.length > 0;
  } else {
    warnings.push(`Community event feeds unavailable (${safeMessage(community.reason)}).`);
    partial = true;
  }
  if (hotstar.status === 'fulfilled' && Array.isArray(hotstar.value?.matches)) {
    matches.push(...hotstar.value.matches);
    const providerWarnings = Array.isArray(hotstar.value.warnings) ? hotstar.value.warnings : [];
    warnings.push(...providerWarnings.map(warning => safeMessage(warning)));
    partial ||= Boolean(hotstar.value.partial) || providerWarnings.length > 0;
  } else {
    const message = hotstar.status === 'rejected' ? safeMessage(hotstar.reason) : 'unexpected catalogue response';
    warnings.push(`JioHotstar live catalogue unavailable (${message}).`);
    partial = true;
  }
  const unique = uniqueMatches(matches);
  return {
    matches: unique,
    warnings: [...new Set(warnings)],
    hotstarCount: unique.filter(match => match.provider === 'JioHotstar').length,
    partial,
  };
}
