// Classify setup failure separately from a feature failure; never turn it into a pass.
export function browserEnvironmentBlock(report) {
  const tests = [];
  const visit = suites => {
    for (const suite of suites ?? []) {
      for (const spec of suite.specs ?? []) tests.push(...(spec.tests ?? []));
      visit(suite.suites);
    }
  };
  visit(report.suites);
  const launchFailed = test => (test.results ?? []).some(result =>
    [result.error, ...(result.errors ?? [])].some(error => /browserType\.launch:/.test(error?.message ?? '')));
  if (!tests.length || !tests.every(launchFailed)) return null;
  return { outcome: 'BLOCKED', scope: 'local demonstrator only', cases_executed: 0,
    reason: 'Chromium could not launch. No browser scenario was executed; inspect playwright.log and playwright.json for environment details.' };
}
