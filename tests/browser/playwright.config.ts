import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: process.env.PROJECT_CHECK_MAP_ONLY === '1' ? 'map.spec.ts' : process.env.PROJECT_CHECK_REPORTER_ONLY === '1' ? 'reporter.spec.ts' : 'acceptance.spec.ts', fullyParallel: false,
  workers: 1, retries: 0, timeout: 15000, expect: { timeout: 2000 },
  outputDir: process.env.PROJECT_CHECK_BROWSER_ARTIFACTS,
  reporter: [
    ['../../assets/playwright/project-check-reporter.ts'],
    ['json', { outputFile: process.env.PROJECT_CHECK_BROWSER_REPORT }],
    ['line'],
  ],
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  use: {
    baseURL: process.env.PROJECT_CHECK_DEMO_URL,
    headless: true, trace: 'on', screenshot: 'only-on-failure',
    launchOptions: process.env.PROJECT_CHECK_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PROJECT_CHECK_CHROMIUM_EXECUTABLE } : {},
  },
});
