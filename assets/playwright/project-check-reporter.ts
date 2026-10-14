import type { Reporter, TestCase, TestResult, TestStep } from '@playwright/test/reporter';

/** Place before the JSON reporter. Records actual API/expect step titles (which may contain test data). */
export default class ProjectCheckReporter implements Reporter {
  onTestEnd(_test: TestCase, result: TestResult) {
    const checks: unknown[] = [];
    const operations = (step: TestStep): unknown[] => step.steps.flatMap(child => [
      ...(['pw:api', 'expect'].includes(child.category) ? [{
        category: child.category, title: child.title, error: Boolean(child.error),
        skipped: child.annotations?.some(a => a.type === 'skip') ?? false,
      }] : []), ...operations(child),
    ]);
    const walk = (steps: TestStep[]) => {
      for (const step of steps) {
        const match = /^pc:(click|fill|selectOption|check|press|goto|reload|assert):([A-Za-z0-9_-]+)$/.exec(step.title);
        if (match && step.category === 'test.step') checks.push({
          id: match[2], kind: match[1], passed: !step.error && !step.annotations?.some(a => a.type === 'skip'),
          operations: operations(step),
        });
        walk(step.steps);
      }
    };
    walk(result.steps);
    result.attachments.push({ name: 'project-check-steps', contentType: 'application/json',
      body: Buffer.from(JSON.stringify({ version: 1, checks })) });
  }
}
