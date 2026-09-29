import test from 'node:test';
import assert from 'node:assert/strict';
import { PhaseRecorder } from '../assets/playwright/phase-rule-engine.mjs';

const logoutRules = [
  { kind: 'response', method: 'POST', urlPattern: '/logout$', status: 204,
    reason: 'logout completed', min: 1, max: 1 },
  { kind: 'response', method: 'GET', urlPattern: '/profile$', status: 401,
    reason: 'protected profile after logout', min: 0 },
  { kind: 'requestfailed', method: 'GET', urlPattern: '/feed$', errorText: 'net::ERR_ABORTED',
    reason: 'navigation canceled pending feed', min: 0 },
];

test('logout phase distinguishes 204, 401 and canceled requests', () => {
  const recorder = new PhaseRecorder();
  recorder.start('logout', logoutRules);
  recorder.record({ kind: 'response', method: 'POST', url: 'https://app.test/logout', status: 204 });
  recorder.record({ kind: 'response', method: 'GET', url: 'https://app.test/profile', status: 401 });
  recorder.record({ kind: 'requestfailed', method: 'GET', url: 'https://app.test/feed', errorText: 'net::ERR_ABORTED' });
  recorder.end('logout');
  const log = recorder.finish();
  assert.deepEqual(log.unexpected, []);
  assert.deepEqual(log.events.map(event => event.matched), [
    'logout completed', 'protected profile after logout', 'navigation canceled pending feed',
  ]);
});

test('same 401 in another phase and unrelated cancellation fail', () => {
  const recorder = new PhaseRecorder();
  recorder.start('login', []);
  recorder.record({ kind: 'response', method: 'GET', url: 'https://app.test/profile', status: 401 });
  recorder.end('login');
  recorder.start('logout', logoutRules);
  recorder.record({ kind: 'response', method: 'POST', url: 'https://app.test/logout', status: 204 });
  recorder.record({ kind: 'requestfailed', method: 'POST', url: 'https://app.test/save', errorText: 'net::ERR_ABORTED' });
  recorder.end('logout');
  assert.equal(recorder.finish().unexpected.length, 2);
});

test('missing required 204 and wrong status fail', () => {
  const recorder = new PhaseRecorder();
  recorder.start('logout', logoutRules);
  recorder.record({ kind: 'response', method: 'POST', url: 'https://app.test/logout', status: 401 });
  recorder.end('logout');
  assert.equal(recorder.finish().unexpected.length, 2);
});

test('broad network rules and nested phases are rejected', () => {
  const recorder = new PhaseRecorder();
  assert.throws(() => recorder.start('logout', [{ kind: 'response', status: 401, reason: 'all errors', min: 0 }]));
  recorder.start('logout', logoutRules);
  assert.throws(() => recorder.start('login', []));
});
