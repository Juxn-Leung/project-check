import { test as base } from '@playwright/test';
import { PhaseRecorder, type PhaseRule } from './phase-rule-engine.mjs';

type PhaseController = {
  run<T>(name: string, rules: PhaseRule[], action: () => Promise<T>, assertState: () => Promise<void>): Promise<T>;
};

/** Import this test object instead of Playwright's base test in browser scenarios. */
export const test = base.extend<{ phase: PhaseController }>({
  phase: async ({ context }, use, testInfo) => {
    const recorder = new PhaseRecorder();
    const bound = new WeakSet<object>();
    const bind = (page: import('@playwright/test').Page) => {
      if (bound.has(page)) return;
      bound.add(page);
      page.on('response', response => recorder.record({
        kind: 'response', method: response.request().method(), url: response.url(), status: response.status(),
      }));
      page.on('requestfailed', request => recorder.record({
        kind: 'requestfailed', method: request.method(), url: request.url(),
        errorText: request.failure()?.errorText ?? 'unknown',
      }));
      page.on('console', message => {
        if (message.type() === 'error') recorder.record({ kind: 'console', message: message.text() });
      });
      page.on('pageerror', error => recorder.record({ kind: 'pageerror', message: error.message }));
    };
    context.on('page', bind);
    for (const page of context.pages()) bind(page);
    await use({
      run: async (name, rules, action, assertState) => {
        if (typeof assertState !== 'function') throw new Error(`${name}: assertState callback is required`);
        recorder.start(name, rules);
        try {
          const result = await action();
          await assertState();
          return result;
        } finally {
          recorder.end(name);
        }
      },
    });
    const log = recorder.finish();
    await testInfo.attach('project-check-browser-events', {
      body: JSON.stringify(log), contentType: 'application/json',
    });
    if (log.unexpected.length) throw new Error(`Unexpected browser events: ${log.unexpected.join('; ')}`);
  },
});

export { expect } from '@playwright/test';
export type { PhaseRule } from './phase-rule-engine.mjs';
