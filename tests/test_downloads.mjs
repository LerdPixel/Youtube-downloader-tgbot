import test from 'node:test';
import assert from 'node:assert/strict';
import { downloadActivities, partitionDownloads } from '../web_app/static/downloads.mjs';

function job(id,status,batch_id=null,day=1,index=1) {
  return {id,status,batch_id,playlist_title:'Одинаковое название плейлиста',playlist_index:index,
    title:`Видео ${id}`,created_at:`2026-10-${String(day).padStart(2,'0')}T12:00:00Z`};
}

test('playlist modal contains only the requested batch, including repeated runs of the same playlist',() => {
  const jobs = [job('a','queued','one'),job('b','complete','one'),job('c','failed','one'),
    job('d','queued','two'),job('e','complete'),job('f','failed','two')];
  const columns = partitionDownloads(jobs,'one');
  assert.deepEqual(columns.active.map(j=>j.id),['a']);
  assert.deepEqual(columns.complete.map(j=>j.id),['b']);
  assert.deepEqual(columns.failed.map(j=>j.id),['c']);
  assert.equal(Object.values(partitionDownloads(jobs,'unknown')).flat().length,0);
});

test('all history statuses belong to exactly one of the three lists',() => {
  const statuses = ['queued','downloading','processing','complete','failed','cancelled','interrupted'];
  const jobs = statuses.map((status,i)=>job(String(i),status));
  const columns = partitionDownloads(jobs);
  assert.equal(columns.active.length,3);
  assert.equal(columns.complete.length,1);
  assert.equal(columns.failed.length,3);
  assert.equal(new Set(Object.values(columns).flat().map(j=>j.id)).size,jobs.length);
});

test('homepage shows three most recent activities, a whole playlist occupies one slot',() => {
  const jobs = [job('old','complete',null,1),job('a','queued','one',3),job('b','failed','one',3),
    job('single','complete',null,4),job('second-playlist','complete','two',2)];
  const recent = downloadActivities(jobs).slice(0,3);
  assert.deepEqual(recent.map(a=>a.batchId || a.jobs[0].id),['single','one','two']);
  assert.equal(recent[1].jobs.length,2);
  assert.equal(downloadActivities(jobs).length,4);
});

test('status changes update counters and current download precedes queued entries',() => {
  const jobs = [job('wait','queued','one',1,1),job('running','downloading','one',1,2)];
  assert.deepEqual(partitionDownloads(jobs,'one').active.map(j=>j.id),['running','wait']);
  jobs[1].status = 'complete';
  const updated = partitionDownloads(jobs,'one');
  assert.equal(updated.active.length,1);
  assert.equal(updated.complete.length,1);
  assert.equal(updated.failed.length,0);
});
