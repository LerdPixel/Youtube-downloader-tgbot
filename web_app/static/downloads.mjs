export const pending = (job) => ['queued', 'downloading', 'processing'].includes(job.status);

export function partitionDownloads(jobs, batchId = null) {
  const columns = { active: [], complete: [], failed: [] };
  for (const job of jobs) {
    if (batchId !== null && job.batch_id !== batchId) continue;
    columns[pending(job) ? 'active' : job.status === 'complete' ? 'complete' : 'failed'].push(job);
  }
  for (const [key, list] of Object.entries(columns)) list.sort((a,b) => {
    if (key === 'active') {
      const running = Number(b.status !== 'queued') - Number(a.status !== 'queued');
      if (running) return running;
    }
    return batchId !== null ? (a.playlist_index || 0) - (b.playlist_index || 0)
      : key === 'active' ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at);
  });
  return columns;
}

export function downloadActivities(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const key = job.batch_id ? `playlist:${job.batch_id}` : `video:${job.id}`;
    if (!groups.has(key)) groups.set(key, {
      id: key, batchId: job.batch_id || null, title: job.batch_id ? job.playlist_title || 'Плейлист' : job.title,
      createdAt: job.created_at, jobs: [],
    });
    const group = groups.get(key);
    group.jobs.push(job);
    if (job.created_at > group.createdAt) group.createdAt = job.created_at;
  }
  return [...groups.values()].sort((a,b) => b.createdAt.localeCompare(a.createdAt));
}
